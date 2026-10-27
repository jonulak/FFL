"""Live points for the season UI: one FastCast subscription per API process, rescored with the
league's stat table, laid over the cached page payloads at serve time.

`Runner` is the background thread. FastCast is NFL-wide, so one socket serves every registered league: each
document is rescored once per league (its own stat table) into that league's points, and each league's starters
feed its own play store (a `Lane`); each league's points are `State.points_for(key)`. It only holds the socket while games are on: inside the
Thursday-to-Monday window (season.is_live) it connects, and once the checkpoint says no game
is in progress and the next kickoff is more than IDLE_BEFORE_KICKOFF away it drops the socket
and sleeps until just before that kickoff. Every checkpoint or patch rescores the document;
when any player's points change, `version` ticks and the /live stream wakes.

The overlay functions are pure and do not care where `points` came from: a player found in
`points` takes that value as `actual`, every side and card total is re-summed. Players absent
from the checkpoint have recorded nothing this week, so ESPN's own zero stands.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone

from . import db, formats
from . import fastcast as fc
from . import playbook
from . import plays as P
from . import season as S

IDLE_BEFORE_KICKOFF = 15 * 60  # connect this long before the next kickoff
ROSTER_REFRESH = 10 * 60  # how often the runner re-reads who starts across the league
OFF_WINDOW_CHECK = 5 * 60  # outside the game window, look at the clock this often
MAX_IDLE = 30 * 60  # never sleep longer than this without re-reading the schedule
# a game reads "pre" until the ball is kicked, minutes after the listed time (hours, in a weather
# delay): stay connected this long past a kickoff that has not happened, then let it go (postponed)
LATE_KICKOFF = 2 * 3600


class State:
    """What the API reads: each league's latest per-player points and a version that ticks on change."""

    def __init__(self):
        self.version = 0
        self.moves = 0  # logged league moves (moved()); the stream sends them as `data: m<n>`
        # every league's {key: (points, week)}, scored off the same documents on its own table. The week is the NFL
        # week the points were scored for: the socket drops after Monday night and the totals stay here until the next
        # kickoff; the week payload rolls over on Tuesday, so the API must know these belong to last week and leave
        # the new week's zeros alone.
        self.leagues: dict[str, tuple[dict[int, float], int | None]] = {}
        self.games: dict[str, dict] = {}  # team -> {status, quarter, clock, score} (fastcast.game_states)
        self.stats: dict[int, dict] = {}  # player -> raw stat row (fastcast.player_stats)
        self.at: float | None = None  # when the points last changed
        self.connected = False
        self.error: str | None = None
        self.cond = threading.Condition()

    def update(self, leagues: dict[str, tuple[dict[int, float], int | None]], games: dict[str, dict] | None = None,
               stats: dict[int, dict] | None = None) -> bool:
        """Store every league's latest (points, week), the game states and stat rows; tick `version` (and wake the
        stream) only when a point moved or a game changed status or quarter. The clock alone
        never wakes the page: every GET reads the stored clock, and the 60 s poll keeps it
        fresh enough. A stat that moves without moving points (an incompletion) rides along."""
        games = games if games is not None else self.games
        with self.cond:
            same_points = ({k: p for k, (p, _) in leagues.items()}
                           == {k: p for k, (p, _) in self.leagues.items()})
            same_games = _sans_clock(games) == _sans_clock(self.games)
            self.games = games
            if stats is not None:
                self.stats = stats
            self.leagues = leagues
            if same_points and same_games and self.version:
                return False
            self.version += 1
            if not same_points:
                self.at = time.time()
            self.cond.notify_all()
            return True

    def points_for(self, key: str) -> dict[int, float]:
        """A league's points; none before the runner has scored it."""
        return self.leagues.get(key, ({}, None))[0]

    def week_for(self, key: str) -> int | None:
        """The NFL week a league's points were scored for."""
        return self.leagues.get(key, ({}, None))[1]

    def moved(self) -> None:
        """A move was logged (api.on_new_rows, once its pages are rebuilt): wake the stream so open pages refetch.
        Its own counter: `version` is the points' and the runner reads 0 there as its first checkpoint."""
        with self.cond:
            self.moves += 1
            self.cond.notify_all()

    def wait_for_tick(self, seen: tuple[int, int], timeout: float) -> tuple[int, int]:
        """Block until (version, moves) moves past `seen` or `timeout` elapses; returns the current pair."""
        with self.cond:
            self.cond.wait_for(lambda: (self.version, self.moves) != seen, timeout=timeout)
            return self.version, self.moves

    def wait_for_change(self, seen: int, timeout: float) -> int:
        """Block until `version` moves past `seen` or `timeout` elapses; returns the version."""
        with self.cond:
            self.cond.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version

    def snapshot(self) -> dict:
        with self.cond:
            return {"version": self.version, "at": self.at, "connected": self.connected,
                    "error": self.error,
                    "leagues": {k: {"players": len(p), "week": w} for k, (p, w) in self.leagues.items()}}


def stream_open(cur: tuple[int, int]) -> str:
    """The /live stream's first message: both counts in one (a live page skips exactly one opening message), so a
    page that reconnects after the phone slept sees the moves count moved while it was away."""
    return f"retry: 3000\ndata: {cur[0]} m{cur[1]}\n\n"


def stream_lines(seen: tuple[int, int], cur: tuple[int, int]) -> list[str]:
    """The /live events between two (version, moves) ticks: `data: m<n>` for a move (the Transactions and Players
    pages refresh only on these), `data: <version>` for points. Live pages refetch on either."""
    out = []
    if cur[1] != seen[1]:
        out.append(f"data: m{cur[1]}\n\n")
    if cur[0] != seen[0]:
        out.append(f"data: {cur[0]}\n\n")
    return out


def _sans_clock(games: dict[str, dict]) -> dict[str, tuple]:
    # a change of possession or a drive into the red zone wakes the page: the board marks both
    return {t: (g.get("status"), g.get("quarter"), g.get("ball"), g.get("red_zone")) for t, g in games.items()}


STATE = State()


def enabled() -> bool:
    """FFL_LIVE=0 keeps the socket closed (tests, a second dev API on the same machine)."""
    return os.environ.get("FFL_LIVE", "1") != "0"


# ---- overlay ----------------------------------------------------------------------------------

def _n(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _parts(stats: dict, spec: list[tuple[str, str]]) -> list[str]:
    """'{value} {label}' for each (stat id, label) whose value is non-zero, in order."""
    return [f"{_n(stats[sid])} {label}" for sid, label in spec if float(stats.get(sid) or 0) != 0]


PASSING = [("3", "yds"), ("4", "TD"), ("20", "INT"), ("19", "2PT")]
RUSHING = [("24", "yds"), ("25", "TD"), ("26", "2PT")]
RECEIVING = [("42", "yds"), ("43", "TD"), ("44", "2PT")]
DEFENCE = [("99", "sk"), ("95", "INT"), ("96", "FR"), ("98", "SFTY")]
DEFENCE_TD = ("101", "102", "103", "104", "93")  # return and blocked-kick touchdowns

# A week's volume, the usage behind the points that scores nothing itself: (stat id, one, many).
# ESPN's 0 pass attempts, 23 rushing attempts, 58 receiving targets.
VOLUME = {"QB": ("0", "pass attempt", "pass attempts"), "RB": ("23", "carry", "carries"),
          "WR": ("58", "target", "targets"), "TE": ("58", "target", "targets")}


def volume(pos: str, stats: dict) -> dict | None:
    """His usage count off the raw stat row ({"count": 9, "label": "targets"}); None for a
    position without one (K, D/ST). A row without the stat counts 0: he played and drew none."""
    if pos not in VOLUME:
        return None
    sid, one, many = VOLUME[pos]
    n = int(float(stats.get(sid) or 0))
    return {"count": n, "label": one if n == 1 else many}


def stat_line(pos: str, stats: dict) -> str | None:
    """A box-score line from the raw stat row, by position — the stats the league scores,
    the way a broadcast ticker abbreviates them. None when nothing that scores has happened.
    Stat ids are ESPN's: 0/1 attempts/completions, 3/4/20 passing, 23/24/25 rushing,
    53/42/43 receiving, 72 fumbles lost, 83/84 FG made/att, 86/87 XP made/att, 85 FG missed,
    99/95/96/98 sacks/INT/FR/safeties, 120 points allowed, 127 yards allowed."""
    groups: list[list[str]] = []
    if pos == "K":
        fga, xpa = stats.get("84") or 0, stats.get("87") or 0
        g = [f"FG {_n(stats.get('83') or 0)}/{_n(fga)}"] if fga else []
        g += [f"XP {_n(stats.get('86') or 0)}/{_n(xpa)}"] if xpa else []
        groups.append(g)
    elif pos == "D/ST":
        g = _parts(stats, DEFENCE)
        tds = sum(float(stats.get(s) or 0) for s in DEFENCE_TD)
        if tds:
            g.append(f"{_n(tds)} TD")
        if stats.get("120") is not None:
            g.append(f"{_n(stats['120'])} PA")
        if stats.get("127") is not None:
            g.append(f"{_n(stats['127'])} YA")
        groups.append(g)
    else:
        if stats.get("0"):
            groups.append([f"{_n(stats.get('1') or 0)}/{_n(stats['0'])}"] + _parts(stats, PASSING))
        rush = _parts(stats, RUSHING)
        if stats.get("23") and (rush or pos == "RB"):
            rush = [f"{_n(stats['23'])} car"] + rush
        rec = _parts(stats, RECEIVING)
        if stats.get("53") or rec:
            rec = [f"{_n(stats.get('53') or 0)} rec"] + rec
        # rushing leads for a running back, receiving for everyone else
        groups += [rush, rec] if pos == "RB" else [rec, rush]
        if stats.get("72"):  # a lost fumble rides on the last group rather than standing alone
            groups = [g for g in groups if g] or [[]]
            groups[-1].append(f"{_n(stats['72'])} FUM")
    text = " · ".join(", ".join(g) for g in groups if g)
    return text or None


# The defence's tiered stats (89-92, 123-125 points allowed; 128-136 yards allowed) are flags: 1
# in the one band the game landed in. The breakdown shows the total they were read off instead.
BAND_BASIS = {**{s: "120" for s in (89, 90, 91, 92, 123, 124, 125)}, **{s: "127" for s in range(128, 137)}}
# Kicking ids sit apart (74-88, then 198-201 for the long field goals); the breakdown reads
# passing, rushing, receiving, kicking, defence, so the long kicks sort beside the short ones.
_ORDER = {198: 80.1, 199: 80.2, 200: 80.3, 201: 80.4}


def _label(raw: str) -> str:
    """ESPN's scoring label as a line item: "Each reception" -> "Reception"."""
    s = raw[5:] if raw.startswith("Each ") else raw
    return s[:1].upper() + s[1:]


def box_items(settings: dict) -> list[dict]:
    """The league's scoring list for box_score: the flat `scoring` list's items (labels), each with `by_pos`,
    its points at each position (fastcast.scoring_table), and any stat that scores only at some positions."""
    table = fc.scoring_table(settings)
    items = {int(x["stat_id"]): {**x} for x in settings["scoring"]}
    for pos, t in table.items():
        for sid in t:
            items.setdefault(sid, {"stat_id": sid, "abbr": formats.abbr(sid), "label": formats.label(sid), "points": table[""].get(sid, 0.0)})
    for sid, x in items.items():
        x["by_pos"] = {pos: t.get(sid, 0.0) for pos, t in table.items() if pos}
    return list(items.values())


def box_score(stats: dict, scoring: list[dict], pos: str | None = None) -> list[dict]:
    """Every stat in the row the league scores, as a line item: the count, the rate (at his position, when
    the item says per position: `box_items`) and the points it earned (count x rate). A band row's count is
    the total it was read off (17 points allowed) and its rate is the flat award. The items sum to what
    score_all gives the row."""
    items = []
    for s in sorted(scoring, key=lambda s: _ORDER.get(int(s["stat_id"]), int(s["stat_id"]))):
        sid = int(s["stat_id"])
        rate = float((s.get("by_pos") or {}).get(pos or "", s["points"]))
        n = float(stats.get(str(sid)) or 0)
        if not n or not rate:
            continue
        band = BAND_BASIS.get(sid)
        items.append({"stat_id": sid, "label": _label(s.get("label") or s.get("abbr") or str(sid)),
                      "count": float(stats.get(band) or 0) if band else n, "rate": rate, "flat": band is not None,
                      "points": round(n * rate, 2)})
    return items


def field(pos: str, team: str | None, game: dict) -> str | None:
    """"red_zone", "on" or None: whether a player's unit is on the field, read off possession.

    An offensive player (and the kicker) is on when his club has the ball; a D/ST when the other
    club does. Red zone is the ball inside the 20 either way, so a D/ST shows it defending its own.
    """
    ball = game.get("ball")
    if not ball or not team:
        return None
    if (ball != team) if pos == "D/ST" else (ball == team):
        return "red_zone" if game.get("red_zone") else "on"
    return None


def overlay_players(players: list[dict], points: dict[int, float], games: dict[str, dict] | None = None,
                    stats: dict[int, dict] | None = None, scoring: list[dict] | None = None) -> list[dict]:
    """Live `actual` for players the feed has scored; `game` (status, quarter, clock, score) for
    players whose pro team has kicked off, so the page can show the clock where the kickoff
    time was, and with it `field` (`field`) while his unit is on; `stat_line` for players with a stat that scores, and with `scoring` (the league's
    stat list, `box_items`: per position) `box`, the line items behind his points. The stat row is
    the feed's while it has him, else ESPN's own (`stat_row`, the week payload's), which is
    dropped from the output either way."""
    out = []
    for p in players:
        espn_row = p.get("stat_row")
        p = {k: v for k, v in p.items() if k != "stat_row"}
        if p.get("player_id") in points:
            p["actual"] = points[p["player_id"]]
        game = (games or {}).get(p.get("team"))
        if game and game.get("status") in ("in", "post"):
            p["game"] = game
            on = field(p.get("pos", ""), p.get("team"), game)
            if on:
                p["field"] = on
        row = (stats or {}).get(p.get("player_id")) or espn_row
        line = stat_line(p.get("pos", ""), row) if row else None
        if line:
            p["stat_line"] = line
        if row and scoring:
            p["box"] = box_score(row, scoring, p.get("pos"))
        if row and (v := volume(p.get("pos", ""), row)):
            p["volume"] = v
        out.append(p)
    return out


def _odds(a: list[dict], b: list[dict], sigma: dict | None) -> float:
    return round(S.win_prob(a, b, sigma or S.SIGMA_FALLBACK), 3)


def overlay_week(payload: dict, points: dict[int, float], games: dict[str, dict] | None = None,
                 stats: dict[int, dict] | None = None, scoring: list[dict] | None = None,
                 sigma: dict | None = None) -> dict:
    """Both sides overlaid, and the board's `win_prob` re-priced off their live points and clocks
    (`sigma`, the calibrated spread per position; the fallback table when None)."""
    out = dict(payload)
    for key in ("me", "opp"):
        if payload.get(key):
            out[key] = overlay_side(payload[key], points, games, stats, scoring)
    if payload.get("win_prob") and out.get("opp"):
        p = _odds(out["me"]["lineup"], out["opp"]["lineup"], sigma)
        out["win_prob"] = {**payload["win_prob"], "me": p, "opp": round(1 - p, 3)}
    return out


def overlay_side(side: dict, points: dict[int, float], games: dict[str, dict] | None = None,
                 stats: dict[int, dict] | None = None, scoring: list[dict] | None = None) -> dict:
    """One build_side side with live points on every group, its total re-summed over the starters."""
    side = {**side, **{g: overlay_players(side.get(g, []), points, games, stats, scoring) for g in ("lineup", "bench", "ir")}}
    side["actual_total"] = round(sum(p["actual"] for p in side["lineup"]), 2)
    return side


def overlay_roster(payload: dict, points: dict[int, float], games: dict[str, dict] | None = None,
                   stats: dict[int, dict] | None = None, scoring: list[dict] | None = None) -> dict:
    return {**payload, "cards": overlay_players(payload.get("cards", []), points, games, stats, scoring)}


def _live_starters(side: dict, points: dict[int, float], games: dict[str, dict]) -> list[dict]:
    """A tile's starters with the feed's points and, once his game is on, its state."""
    out = []
    for s in side.get("starters") or []:
        s = {**s, "actual": points.get(s["player_id"], s["actual"])}
        g = games.get(s.get("team"))
        if g and g.get("status") in ("in", "post"):
            s["game"] = g
        out.append(s)
    return out


def overlay_league(payload: dict, points: dict[int, float], games: dict[str, dict] | None = None,
                   sigma: dict | None = None) -> dict:
    """Each scoreboard side re-summed over its `starter_ids` when any of them has live points, and
    each matchup `live` while a starter on either side has his game in progress — not merely
    while some game in the league is on. Each side's `starters` carry the feed's points and game
    states, which the tile's estimated final is summed from (web/lib/game.ts sideTotals, as on the
    Matchup board), and a matchup with odds has them re-priced off those."""
    board = []
    for m in payload.get("scoreboard", []):
        row = dict(m)
        for key in ("home", "away"):
            side = m[key]
            ids = side.get("starter_ids") or []
            if any(i in points for i in ids):
                side = {**side, "score": round(sum(points.get(i, 0.0) for i in ids), 2)}
            if side.get("starters"):
                side = {**side, "starters": _live_starters(side, points, games or {})}
            if side is not m[key]:
                row[key] = side
        row["live"] = any((games or {}).get(t, {}).get("status") == "in"
                          for key in ("home", "away") for t in m[key].get("starter_teams") or [])
        row["final"] = S.matchup_final(m, games)
        if m.get("win_prob"):
            p = _odds(row["away"]["starters"], row["home"]["starters"], sigma)
            row["win_prob"] = {"away": p, "home": round(1 - p, 3)}
        board.append(row)
    return {**payload, "scoreboard": board}


def game_on(sides: list[dict | None], games: dict[str, dict] | None) -> bool:
    """Whether a board's game is live: a starter on either side (each `lineup` of player dicts with
    a pro `team`) has his game in progress. The rule a League tile is marked by, for the /plays page,
    which carries no lineups of its own."""
    return any((games or {}).get(p.get("team"), {}).get("status") == "in"
               for s in sides if s for p in s.get("lineup") or [])


# ---- the runner -------------------------------------------------------------------------------

def idle_for(doc: dict, now: datetime | None = None) -> float | None:
    """Seconds to drop the socket for, or None to stay connected.

    Connected while a game is in progress or a kickoff is close; otherwise sleep until just
    before the next kickoff (capped so a schedule change is noticed), or the cap when the
    checkpoint lists no game still to come. A game past its listed kickoff but not yet started
    counts as the next kickoff, so the socket is up when the ball is kicked.
    """
    now = now or datetime.now(timezone.utc)
    if fc.games_in_progress(doc):
        return None
    nxt = fc.next_kickoff(doc, now - timedelta(seconds=LATE_KICKOFF))
    if nxt is None:
        return float(MAX_IDLE)
    until = (nxt - now).total_seconds() - IDLE_BEFORE_KICKOFF
    if until <= 0:
        return None
    return min(until, float(MAX_IDLE))


@dataclass
class Lane:
    """One league's share of the socket: its stat table, its starters (`week_fn`, as `Runner` takes it), and the
    play store of the week they start in."""
    key: str
    ref: object
    table: dict[str, dict[int, float]]
    week_fn: Callable[[], dict] | None
    roster: dict[int, dict] = dc_field(default_factory=dict)
    week: int | None = None
    roster_at: float = 0.0
    store: P.Store | None = None

    def tag(self, msg: str) -> str:
        """An error of this league's as /live/status shows it."""
        return f"{self.key}: {msg}"


class Runner(threading.Thread):
    """Every league as (ref, settings, week_fn), scored on its own table: `week_fn` returns the current week's
    starters in every game (`espn_season.league_starters`, cached by the API); the league's play feed is derived for
    them. None (tests, a dev API) means no feed."""

    def __init__(self, state: State = STATE, leagues: list[tuple] = ()):
        super().__init__(daemon=True, name="live-fastcast")
        self.state = state
        self.stop = threading.Event()
        self.pause = threading.Event()  # set by _on_doc to drop the socket until the next kickoff
        self.idle_until: float | None = None
        # errors surface on /live/status as they happen: a callback that raises on every doc
        # would otherwise read as "connected" while the client reconnects in a loop
        self.client = fc.Client(self._on_doc, on_error=lambda e: setattr(self.state, "error", e))
        self.lanes = [Lane(ref.key, ref, fc.scoring_table(s), fn) for ref, s, fn in leagues]
        self.plays = P.PlayCache()

    def lane(self, key: str) -> Lane | None:
        """The lane of the league `key` names; None when the runner does not score it."""
        return next((ln for ln in self.lanes if ln.key == key), None)

    def _refresh_roster(self) -> None:
        for lane in self.lanes:
            self._refresh_lane(lane)

    def _refresh_lane(self, lane: Lane) -> None:
        if lane.week_fn is None or time.time() - lane.roster_at < ROSTER_REFRESH:
            return
        lane.roster_at = time.time()
        try:
            week = lane.week_fn()
        except Exception as e:  # noqa: BLE001 — the feed keeps the last roster it had
            self.state.error = lane.tag(f"roster refresh failed: {e}")
            return
        lane.roster = P.roster_index(week)
        if week.get("week") != lane.week:
            try:
                store = P.Store(week["week"], ref=lane.ref)  # before the week advances: a failed read must retry
            except (db.DBError, db.SchemaError) as e:  # a stale schema is a RuntimeError, not a DBError
                self.state.error = lane.tag(f"plays store not loaded: {e}")
                return
            lane.week, lane.store = week["week"], store
            if (self.state.error or "").startswith(lane.tag("plays store not loaded")):
                self.state.error = None  # the load recovered; leave any other error alone

    def _on_doc(self, doc: dict) -> None:
        self._refresh_roster()
        first = self.state.version == 0
        befores = [dict(self.state.points_for(ln.key)) for ln in self.lanes]
        stats_before = self.state.stats  # replaced, not mutated, by update: a D/ST's play is read off what moved
        afters = [fc.score_all(doc, ln.table) for ln in self.lanes]
        if not first:
            # a player who drops out of one doc keeps his total: read as 0, his whole total posted as one play when
            # a fuller doc brought him back (HOU-NYG's starters, 2026-10-25 21:00, hours after the final). Each league
            # on its own points, while its stored week is still the lane's (a new week starts from the doc alone)
            afters = [{**{p: v for p, v in before.items() if p not in after}, **after}
                      if self.state.week_for(ln.key) == ln.week else after
                      for ln, before, after in zip(self.lanes, befores, afters)]
        self.state.update({ln.key: (a, ln.week) for ln, a in zip(self.lanes, afters)}, fc.game_states(doc),
                          fc.player_stats(doc))
        self.plays.remember(doc)  # before the events: the play that moved a player is in this doc
        # the first checkpoint after boot is a baseline, not a burst of "events" for every player
        if not first:
            now = datetime.now()
            # the doc's plays are the NFL's, one table for every league, filed once under the newest week a lane has
            # rolled to: a lane still on another week (its starters not rolled over) records none of them, or it would
            # refile them under its own
            nfl_week = max((ln.week for ln in self.lanes if ln.week is not None), default=None)
            entries = playbook.from_fastcast(doc, nfl_week, now) if nfl_week is not None else []
            for lane, before, after in zip(self.lanes, befores, afters):
                if lane.store is None:
                    continue
                self._feed(lane, before, after, doc, now, stats_before, entries if lane.week == nfl_week else [])
        idle = idle_for(doc)
        if idle is not None:
            self.idle_until = time.time() + idle
            self.pause.set()

    def _feed(self, lane: Lane, before: dict, after: dict, doc: dict, now: datetime, stats_before: dict,
              entries: list[dict]) -> None:
        rows = P.events_from(before, after, doc, lane.roster, now, lane.week, self.plays, stats_before)
        try:
            # the plays first, so a row on one reads its wording from the play; then what a new row
            # kept him on a play called back
            lane.store.record(entries, lane.roster, lane.table, seen=now)
            lane.store.add(rows)
            lane.store.record([], lane.roster, lane.table, also={str(r["play_id"]) for r in rows if r.get("play_id")})
            # only a write that happened proves the DB recovered: add([]) writes nothing
            if rows and (self.state.error or "").startswith(lane.tag("plays not saved")):
                self.state.error = None  # leave any other error alone
        except (db.DBError, db.SchemaError) as e:  # the board keeps the rows in memory; a DB hiccup must not drop the feed
            self.state.error = lane.tag(f"plays not saved: {e}")

    def run(self) -> None:
        while not self.stop.is_set():
            if not S.is_live():
                self.stop.wait(OFF_WINDOW_CHECK)
                continue
            self.pause.clear()
            self.state.connected = True
            try:
                self.client.run(self._halt)
            finally:
                self.state.connected = False
                self.state.error = self.client.last_error
            if self.stop.is_set():
                break
            # dropped on purpose: sleep until just before the next kickoff, then reconnect
            self.stop.wait(max(0.0, (self.idle_until or time.time()) - time.time()))

    @property
    def _halt(self) -> threading.Event:
        """The client stops on either signal; a combined event keeps its loop to one flag."""
        return _Either(self.stop, self.pause)


class _Either:
    """Just enough of threading.Event for fc.Client: set when either underlying event is."""

    def __init__(self, *events: threading.Event):
        self.events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self.events)

    def wait(self, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.is_set():
                return True
            time.sleep(min(0.5, end - time.time()))
        return self.is_set()
