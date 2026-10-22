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

import threading
from datetime import datetime

from . import db


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


def _offence(play: dict) -> str | None:
    """The pro team id (ESPN's competitor id, "16") that had the ball at the snap."""
    team = (play.get("start") or {}).get("team")
    return str(team.get("id") if isinstance(team, dict) else team) if team is not None else None


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
            offence = _offence(play)
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


def taken_back(before: dict | None, after: dict | None) -> bool:
    """Whether a fall in a player's points erased an earlier play rather than scoring a new one:
    a counting stat fell (a catch called back), or no stat rose at all (yards trimmed back to a
    foul spot, a stat correction). A new play, even one for a loss, raises a count."""
    before, after = before or {}, after or {}
    fell = any(float(after.get(s) or 0) < float(before.get(s) or 0) for s in COUNTERS)
    rose = any(float(v or 0) > float(before.get(s) or 0) for s, v in after.items())
    return fell or not rose


def events_from(before: dict[int, float], after: dict[int, float], doc: dict, roster: dict[int, dict],
                at: datetime, week: int, cache: PlayCache | None = None,
                stats_before: dict[int, dict] | None = None) -> list[dict]:
    """The play-feed rows for one rescoring: every rostered starter whose points moved.

    `match` says where the caption came from: "player" (a remembered play naming him),
    "defence" (a D/ST's play, read off which of its stats moved since `stats_before`),
    "last" (the game's current last play, which may already be the next one) or "reversed"
    (points taken back, `taken_back`: the play called back naming him, else no caption)."""
    rows = []
    for pid, who in roster.items():
        new, old = after.get(pid), before.get(pid)
        if new is None or new == (old if old is not None else 0.0):
            continue
        game = _game_of(doc, pid) or {}
        row = doc.get("statistics", {}).get(str(pid)) or {}
        play, match = None, "last"
        if cache is not None and stats_before is not None and new < (old or 0.0) \
                and taken_back(stats_before.get(pid), row.get("stats")):
            # no caption beats the next play's: it is what the feed shows when none says it was called back
            play, match = cache.called_back(game.get("id"), pid) or {}, "reversed"
        elif cache is not None and who["pos"] == "D/ST":
            moved = _moved((stats_before or {}).get(pid), row.get("stats"))
            team = row.get("proTeamId")
            play = cache.defence_play(game.get("id"), str(team) if team is not None else None, moved)
            match = "defence" if play else "last"
        elif cache is not None:
            play = cache.play_for(game.get("id"), pid)
            match = "player" if play else "last"
        if match != "reversed":
            play = play or game.get("lastPlay") or {}
        rows.append({"at": at.isoformat(timespec="seconds"), "week": week, "player_id": pid, **who,
                     "delta": round(new - (old or 0.0), 2), "total": round(new, 2),
                     "text": (play.get("text") or "").strip() or None, "play_id": play.get("playId"),
                     "quarter": play.get("period") or game.get("period"), "clock": _clock(play, game),
                     "game_id": game.get("id"), "match": match})
    return rows


class Store:
    """The week's events, newest last in memory (the live path reads it every few seconds),
    written through to the DB as they happen and reloaded from it at boot."""

    def __init__(self, week: int):
        self.week = week
        self._lock = threading.Lock()
        self.rows: list[dict] = db.plays_rows(week)

    def add(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._lock:
            self.rows.extend(rows)  # memory first: the live baseline has moved on, so these must not be lost
        db.add_plays(rows)  # outside the lock: a slow or failed write must not block readers; errors propagate

    def latest(self, n: int | None = None, teams: set[int] | None = None) -> list[dict]:
        """Newest first; with `teams`, only the rows scored for those fantasy teams (one board)."""
        with self._lock:
            rows = self.rows[::-1]
        if teams is not None:
            rows = [r for r in rows if r.get("team_id") in teams]
        return rows[:n] if n else rows
