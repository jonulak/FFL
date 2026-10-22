"""The play feed: one event per points change of any league starter, from the live feed.

An event is derived, not received: the feed pushes a stats patch for a player, `live` rescores
him, and the difference is the event. The play text comes from `PlayCache`: the stats and the
play text travel in different frames — the play first, the stats one to three frames later,
by which time the game's `lastPlay` is often already the *next* play (a field goal was once
captioned with the timeout after it). Every play the feed shows (`lastPlay`, the current
drive's `plays`, the game's `scoringPlays`) names its players by id, so the cache remembers
them per game and an event takes the newest remembered play naming the player; `lastPlay` is
only the fallback (stat corrections, a yardage band). A play names the defenders, never the
D/ST, so a D/ST row is matched by what moved in its stat row instead: a sack takes the newest
remembered play the other club ran that reads "sacked", a turnover "INTERCEPTED" or
"RECOVERED by", points allowed the other club's newest scoring play (`DEFENCE_TEXT`).
Events are written to the plays table (ffl/db.py) and a week's are reloaded at boot, so a deploy mid-slate does not empty the feed.

Every matchup's starters are followed, so every board has a feed. A row carries the fantasy
team it scored for (`team_id`), not a side: a team plays one game a week, so a board takes the
rows of its two teams (`Store.latest(teams=)`) and `for_board` says which side each is on.
"""
from __future__ import annotations

import re
import threading
from collections import defaultdict
from datetime import datetime

from . import db, playbook


def roster_index(league: dict) -> dict[int, dict]:
    """player_id -> {name, pos, team, team_id} for every starter in every game of a
    `espn_season.league_starters` payload."""
    out: dict[int, dict] = {}
    for game in league.get("games", []):
        for s in game:
            for p in s.get("lineup", []):
                out[p["player_id"]] = {"name": p["name"], "pos": p["pos"], "team": p["team"], "team_id": s["team_id"]}
    return out


def for_board(rows: list[dict], left: int) -> list[dict]:
    """The rows with `side` set for one board: "me" for the left team (`left`), else "opp".
    Rows written before the feed went league-wide carry a side already; the board's wins."""
    return [{**r, "side": "me" if r.get("team_id") == left else "opp"} for r in rows]


def _game_of(doc: dict, player_id: int) -> dict | None:
    gid = (doc.get("statistics", {}).get(str(player_id)) or {}).get("gameId")
    return next((e for e in doc.get("events", []) if e.get("id") == gid), None) if gid else None


def _clock(play: dict, game: dict) -> str | None:
    """A play's clock is seconds remaining (560.0); the game's is already 'm:ss'."""
    secs = play.get("clock")
    if isinstance(secs, (int, float)):
        return f"{int(secs) // 60}:{int(secs) % 60:02d}"
    return game.get("clock")


# D/ST stat id -> what the play's text says when that stat moves (live.DEFENCE and the TDs)
DEFENCE_TEXT = {"99": "sacked", "95": "INTERCEPTED", "96": "RECOVERED by", "98": "SAFETY", "97": "BLOCKED",
                **{s: "TOUCHDOWN" for s in ("93", "94", "101", "102", "103", "104")}}
POINTS_ALLOWED = "120"


class PlayCache:
    """The last `keep` plays of each game, by play id, gathered from every document seen.

    A drive's `plays` list is replaced when the drive ends, so the play that ended it (a field
    goal, a turnover) is gone from the doc before its stats arrive; the cache keeps it.
    """

    def __init__(self, keep: int = 80):
        self.keep = keep
        self.plays: dict[str, dict[str, dict]] = {}  # game id -> play id -> play

    def remember(self, doc: dict) -> None:
        for game in doc.get("events", []):
            seen = [game.get("lastPlay") or {}]
            seen += (game.get("drive") or {}).get("plays") or []
            seen += game.get("scoringPlays") or []
            known = self.plays.setdefault(game.get("id"), {})
            for play in seen:
                if play.get("playId"):
                    known[str(play["playId"])] = play
            if len(known) > self.keep:
                for pid in sorted(known, key=int)[:-self.keep]:
                    del known[pid]

    def play_for(self, game_id: str, player_id: int) -> dict | None:
        """The newest remembered play of the game that names the player."""
        known = self.plays.get(game_id) or {}
        for pid in sorted(known, key=int, reverse=True):
            if any(str(p.get("playerId")) == str(player_id) for p in known[pid].get("players") or []):
                return known[pid]
        return None

    def called_back(self, game_id: str, player_id: int) -> dict | None:
        """The newest remembered play of the game naming the player whose text says it no longer
        counts (a penalty's "No Play", "NULLIFIED", a review "REVERSED")."""
        known = self.plays.get(game_id) or {}
        for pid in sorted(known, key=int, reverse=True):
            play = known[pid]
            text = (play.get("text") or "").lower()
            if any(w in text for w in CALLED_BACK) and \
                    any(str(p.get("playerId")) == str(player_id) for p in play.get("players") or []):
                return play
        return None

    def defence_play(self, game_id: str, team: str | None, moved: set[str]) -> dict | None:
        """The newest remembered play the D/ST's opponent ran (`team` is the D/ST's club id)
        whose text says what `moved` in its stat row; a points-allowed move takes the newest
        scoring play. None when nothing moved that a play can be found for (a yardage band)."""
        words = [DEFENCE_TEXT[s].lower() for s in moved if s in DEFENCE_TEXT]
        scored = POINTS_ALLOWED in moved
        known = self.plays.get(game_id) or {}
        for pid in sorted(known, key=int, reverse=True):
            play = known[pid]
            offence = playbook.offence(play)
            if team is not None and offence is not None and offence == team:
                continue
            text = (play.get("text") or "").lower()
            if any(w in text for w in words) or (scored and play.get("scoringPlay")):
                return play
        return None


def _moved(before: dict | None, after: dict | None) -> set[str]:
    before, after = before or {}, after or {}
    return {s for s, v in after.items() if v != before.get(s, 0)}


# Stats that only count up during a game (ESPN ids, as in live.stat_line): attempts, completions,
# carries, receptions, targets, TDs, 2PTs, interceptions, fumbles lost, kicks, a D/ST's takeaways
# and scores. Yards fall on a play for a loss; the yardage bands flip; these never fall but by a
# play being erased.
COUNTERS = frozenset({"0", "1", "4", "19", "20", "23", "25", "26", "43", "44", "53", "58", "72",
                      "83", "84", "85", "86", "87", *DEFENCE_TEXT})
# What a play's text says once it no longer counts, or once a review overturned it
CALLED_BACK = ("no play", "nullified", "reversed")


# What his newest play says when it lost yards: then a fall with no count moved is that play's
# yards landing a frame ahead of its carry (the feed patches stat by stat), not a revision. Weeks
# 1-6 never showed a play split that way; the guard is cheap.
LOSS_TEXT = ("for -", "kneels")


def _n(v) -> float:
    return float(v or 0)


def revision(before: dict | None, after: dict | None, delta: float, play: dict | None = None,
             dst: bool = False) -> str | None:
    """How a points change that scored no new play reads (a new play moves a count): "reversed"
    when it erased one (a count fell: a catch called back; or yards fell on his newest play
    (`play`) and it drew a flag: trimmed back to the foul's spot), "adjusted" when only yards
    moved (the ball re-spotted, 55 yards to 54, either way). None for a new play. A D/ST's yards
    and points allowed move with no count, so only a count falling revises one."""
    before, after = before or {}, after or {}
    if any(_n(after.get(s)) < _n(before.get(s)) for s in COUNTERS):
        return "reversed" if delta < 0 else None
    if dst or any(_n(after.get(s)) != _n(before.get(s)) for s in COUNTERS):
        return None
    text = ((play or {}).get("text") or "").lower()
    if delta < 0 and "penalty on" in text:
        return "reversed"
    if delta < 0 and any(w in text for w in LOSS_TEXT):
        return None
    return "adjusted"


_GAINED = re.compile(r"\bfor ([1-9]\d*) yards?", re.I)
OLD_FIX_SECS = 30  # weeks 1-6: a re-spot landed within ~25 s of the play; later, two carries often shared a caption


def old_revisions(rows: list[dict]) -> list[dict]:
    """Rows written before `revision` (no stats kept), retagged where the text alone is plain: a change of
    half a point or less, unlike the first, within OLD_FIX_SECS of the first row on the same play, which gained
    yards, for a player who is not a kicker or D/ST (an extra point was often captioned with the TD). Yards
    falling on a flagged play are "reversed", any other such change "adjusted". Oldest first in, the retagged
    rows out."""
    first: dict[tuple, dict] = {}
    out = []
    for r in rows:
        key, text = (r["player_id"], r.get("play_id")), r.get("text") or ""
        was = first.get(key)
        if was and r.get("match") == "player" and abs(r["delta"]) <= 0.5 and r["delta"] != was["delta"] \
                and (datetime.fromisoformat(r["at"]) - datetime.fromisoformat(was["at"])).total_seconds() <= OLD_FIX_SECS \
                and r["pos"] not in ("K", "D/ST") and _GAINED.search(text) and not playbook.called_back(text):
            out.append({**r, "match": "reversed" if r["delta"] < 0 and "penalty on" in text.lower() else "adjusted"})
        if r.get("play_id"):
            first.setdefault(key, r)
    return out


def events_from(before: dict[int, float], after: dict[int, float], doc: dict, roster: dict[int, dict],
                at: datetime, week: int, cache: PlayCache | None = None,
                stats_before: dict[int, dict] | None = None) -> list[dict]:
    """The play-feed rows for one rescoring: every rostered starter whose points moved.

    `match` says where the caption came from: "player" (a remembered play naming him),
    "defence" (a D/ST's play, read off which of its stats moved since `stats_before`),
    "last" (the game's current last play, which may already be the next one), "reversed"
    (points taken back, `revision`: the play called back naming him, else the flagged play, else
    no caption) or "adjusted" (his yards re-spotted: his newest play, else no caption)."""
    rows = []
    for pid, who in roster.items():
        new, old = after.get(pid), before.get(pid)
        if new is None or new == (old if old is not None else 0.0):
            continue
        game = _game_of(doc, pid) or {}
        row = doc.get("statistics", {}).get(str(pid)) or {}
        play, match = None, "last"
        named = cache.play_for(game.get("id"), pid) if cache is not None else None
        kind = revision(stats_before.get(pid), row.get("stats"), new - (old or 0.0), named, who["pos"] == "D/ST") \
            if cache is not None and stats_before is not None else None
        # no caption beats the next play's: it is what the feed shows when none is the play revised
        if kind == "reversed":
            flagged = named if "penalty on" in ((named or {}).get("text") or "").lower() else None
            play, match = cache.called_back(game.get("id"), pid) or flagged or {}, "reversed"
        elif kind == "adjusted":
            play, match = named or {}, "adjusted"
        elif cache is not None and who["pos"] == "D/ST":
            moved = _moved((stats_before or {}).get(pid), row.get("stats"))
            team = row.get("proTeamId")
            play = cache.defence_play(game.get("id"), str(team) if team is not None else None, moved)
            match = "defence" if play else "last"
        elif cache is not None:
            play = named
            match = "player" if play else "last"
        if match not in ("reversed", "adjusted"):
            play = play or game.get("lastPlay") or {}
        rows.append({"at": at.isoformat(timespec="seconds"), "week": week, "player_id": pid, **who,
                     "delta": round(new - (old or 0.0), 2), "total": round(new, 2),
                     "text": (play.get("text") or "").strip() or None, "play_id": play.get("playId"),
                     "quarter": play.get("period") or game.get("period"), "clock": _clock(play, game),
                     "game_id": game.get("id"), "match": match})
    return rows


def fold_called_back(rows: list[dict], called: dict[tuple[str, int], dict]) -> list[dict]:
    """The feed rows (newest first) with each called-back row folded into the newest row of his on that
    play, as `called_back: {would_be, kept}` (Purdy's scramble: one row, +3.5 kept of +10.5, not a +3.5 and
    a struck +10.5 the strip could not tell apart); a called-back row with no row of his on its play stands
    alone. A row carrying one is a revision: it reads the play's newest wording."""
    out, folded = [], set()
    for r in rows:
        key = (str(r.get("play_id")), r.get("player_id"))
        if r.get("play_id") and key in called and key not in folded:
            folded.add(key)
            r = {**r, "called_back": {"would_be": called[key]["would_be"], "kept": called[key]["kept"]}}
        out.append(r)
    return out + [r for k, r in called.items() if k not in folded]


class Store:
    """The week's events, newest last in memory (the live path reads it every few seconds),
    written through to the DB as they happen and reloaded from it at boot. Beside them the week's
    NFL plays (`book`, ffl/playbook.py), whose text a row on a play shows, and the plays called back
    that cost a starter points (`called`, by play and player), merged into the feed by time."""

    def __init__(self, week: int):
        self.week = week
        self._lock = threading.Lock()
        self._record = threading.Lock()  # one record() at a time: the live feed, the NFL tab, the tick
        self.rows: list[dict] = db.plays_rows(week)
        self.book: dict[str, dict] = {p["play_id"]: p for p in db.nfl_plays_rows(week)}
        self.called: dict[tuple[str, int], dict] = {(r["play_id"], r["player_id"]): r for r in db.called_back_rows(week)}

    def add(self, rows: list[dict]) -> None:
        """Rows on a stored play keep no text of their own: `latest` reads it from the play."""
        if not rows:
            return
        with self._lock:
            rows = [{**r, "text": None} if r.get("play_id") and str(r["play_id"]) in self.book else r for r in rows]
            self.rows.extend(rows)  # memory first: the live baseline has moved on, so these must not be lost
        db.add_plays(rows)  # outside the lock: a slow or failed write must not block readers; errors propagate

    def latest(self, n: int | None = None, teams: set[int] | None = None) -> list[dict]:
        """Newest first; with `teams`, only the rows scored for those fantasy teams (one board). A row
        on a stored play shows its wording as it read when the row happened (`playbook.wording`)."""
        with self._lock:
            rows, book, called = self.rows[::-1], self.book, dict(self.called)
        rows = fold_called_back(rows, called)
        rows.sort(key=lambda r: r.get("at") or "", reverse=True)  # stable: rows of one second stay newest first
        if teams is not None:
            rows = [r for r in rows if r.get("team_id") in teams]
        rows = rows[:n] if n else rows
        return [{**r, "text": playbook.wording(book[str(r["play_id"])], r)}
                if r.get("play_id") and r.get("match") != "correction" and str(r["play_id"]) in book else r
                for r in rows]

    def record(self, entries: list[dict], roster: dict[int, dict], table: dict[int, float],
               also: set[str] | None = None, seen: datetime | None = None) -> None:
        """Store the plays that are new or changed (playbook.changes), a new wording added to the play's
        versions as first `seen` (now), then rewrite the called-back rows of those plays and of `also`
        (plays a new feed row just landed on: what a play kept him moved)."""
        seen_at = (seen or datetime.now()).isoformat(timespec="seconds")
        with self._record:
            fresh = [playbook.versioned(self.book.get(e["play_id"]), e, seen_at) for e in entries
                     if playbook.changes(self.book.get(e["play_id"]), e)]
            if fresh:
                with self._lock:  # memory first, as add: a failed write must not leave a row without its wording
                    self.book.update({e["play_id"]: e for e in fresh})
                db.put_nfl_plays(fresh)
            ids = {e["play_id"] for e in fresh} | {str(i) for i in also or () if i}
            ids &= self.book.keys()
            if not ids or not roster:  # no starters read: keep the rows rather than drop them all
                return
            kept: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
            for r in self.rows:
                if str(r.get("play_id")) in ids:
                    kept[str(r["play_id"])][r["player_id"]] += r.get("delta") or 0.0
            put, drop = [], []
            for pid in ids:
                was = {k: r for k, r in self.called.items() if k[0] == pid}
                now = {(pid, r["player_id"]): {**r, "text": None}  # the play's wording is read from the play
                       for r in playbook.callbacks(self.book[pid], roster, kept[pid], table)}
                for k, r in now.items():
                    r = {**r, "at": was[k]["at"]} if k in was and was[k].get("at") else r  # first seen stands
                    if was.get(k) != r:
                        put.append(r)
                drop += [k for k in was if k not in now]
            if not put and not drop:
                return
            db.put_called_back(put, drop)
            with self._lock:
                self.called.update({(r["play_id"], r["player_id"]): r for r in put})
                for k in drop:
                    self.called.pop(k, None)
