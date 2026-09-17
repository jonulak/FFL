"""Live points for the season UI: one FastCast subscription per API process, rescored with the
league's stat table, laid over the cached page payloads at serve time.

`Runner` is the background thread. It only holds the socket while games are on: inside the
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
from datetime import datetime, timezone

from . import fastcast as fc
from . import plays as P
from . import season as S
from .settings import load

IDLE_BEFORE_KICKOFF = 15 * 60  # connect this long before the next kickoff
ROSTER_REFRESH = 10 * 60  # how often the runner re-reads who starts in my matchup
OFF_WINDOW_CHECK = 5 * 60  # outside the game window, look at the clock this often
MAX_IDLE = 30 * 60  # never sleep longer than this without re-reading the schedule


class State:
    """What the API reads: the latest per-player points and a version that ticks on change."""

    def __init__(self):
        self.version = 0
        self.points: dict[int, float] = {}
        self.games: dict[str, dict] = {}  # team -> {status, quarter, clock, score} (fastcast.game_states)
        self.stats: dict[int, dict] = {}  # player -> raw stat row (fastcast.player_stats)
        # the NFL week the points were scored for. The socket drops after Monday night and the
        # totals stay here until the next kickoff; the week payload rolls over on Tuesday, so
        # the API must know these belong to last week and leave the new week's zeros alone.
        self.week: int | None = None
        self.at: float | None = None  # when the points last changed
        self.connected = False
        self.error: str | None = None
        self.cond = threading.Condition()

    def update(self, points: dict[int, float], games: dict[str, dict] | None = None,
               stats: dict[int, dict] | None = None, week: int | None = None) -> bool:
        """Store the latest points, game states and stat rows; tick `version` (and wake the
        stream) only when a point moved or a game changed status or quarter. The clock alone
        never wakes the page: every GET reads the stored clock, and the 60 s poll keeps it
        fresh enough. A stat that moves without moving points (an incompletion) rides along."""
        games = games if games is not None else self.games
        with self.cond:
            same_points = points == self.points
            same_games = _sans_clock(games) == _sans_clock(self.games)
            self.games = games
            if stats is not None:
                self.stats = stats
            if week is not None:
                self.week = week
            if same_points and same_games and self.version:
                return False
            self.points, self.version = points, self.version + 1
            if not same_points:
                self.at = time.time()
            self.cond.notify_all()
            return True

    def wait_for_change(self, seen: int, timeout: float) -> int:
        """Block until `version` moves past `seen` or `timeout` elapses; returns the version."""
        with self.cond:
            self.cond.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version

    def snapshot(self) -> dict:
        with self.cond:
            return {"version": self.version, "at": self.at, "connected": self.connected,
                    "error": self.error, "players": len(self.points), "week": self.week}


def _sans_clock(games: dict[str, dict]) -> dict[str, tuple]:
    return {t: (g.get("status"), g.get("quarter")) for t, g in games.items()}


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


def overlay_players(players: list[dict], points: dict[int, float], games: dict[str, dict] | None = None,
                    stats: dict[int, dict] | None = None) -> list[dict]:
    """Live `actual` for players the feed has scored; `game` (status, quarter, clock, score) for
    players whose pro team has kicked off, so the page can show the clock where the kickoff
    time was; `stat_line` for players with a stat that scores."""
    out = []
    for p in players:
        if p.get("player_id") in points:
            p = {**p, "actual": points[p["player_id"]]}
        game = (games or {}).get(p.get("team"))
        if game and game.get("status") in ("in", "post"):
            p = {**p, "game": game}
        row = (stats or {}).get(p.get("player_id"))
        line = stat_line(p.get("pos", ""), row) if row else None
        if line:
            p = {**p, "stat_line": line}
        out.append(p)
    return out


def overlay_week(payload: dict, points: dict[int, float], games: dict[str, dict] | None = None,
                 stats: dict[int, dict] | None = None) -> dict:
    out = dict(payload)
    for key in ("me", "opp"):
        side = payload.get(key)
        if not side:
            continue
        side = {**side, **{g: overlay_players(side.get(g, []), points, games, stats) for g in ("lineup", "bench", "ir")}}
        side["actual_total"] = round(sum(p["actual"] for p in side["lineup"]), 1)
        out[key] = side
    return out


def overlay_roster(payload: dict, points: dict[int, float], games: dict[str, dict] | None = None,
                   stats: dict[int, dict] | None = None) -> dict:
    return {**payload, "cards": overlay_players(payload.get("cards", []), points, games, stats)}


def overlay_league(payload: dict, points: dict[int, float]) -> dict:
    """Each scoreboard side re-summed over its `starter_ids` when any of them has live points."""
    board = []
    for m in payload.get("scoreboard", []):
        row = {}
        for key, side in m.items():
            ids = side.get("starter_ids") or []
            if any(i in points for i in ids):
                side = {**side, "score": round(sum(points.get(i, 0.0) for i in ids), 1)}
            row[key] = side
        board.append(row)
    return {**payload, "scoreboard": board}


# ---- the runner -------------------------------------------------------------------------------

def idle_for(doc: dict, now: datetime | None = None) -> float | None:
    """Seconds to drop the socket for, or None to stay connected.

    Connected while a game is in progress or a kickoff is close; otherwise sleep until just
    before the next kickoff (capped so a schedule change is noticed), or the cap when the
    checkpoint lists no game still to come.
    """
    now = now or datetime.now(timezone.utc)
    if fc.games_in_progress(doc):
        return None
    nxt = fc.next_kickoff(doc, now)
    if nxt is None:
        return float(MAX_IDLE)
    until = (nxt - now).total_seconds() - IDLE_BEFORE_KICKOFF
    if until <= 0:
        return None
    return min(until, float(MAX_IDLE))


class Runner(threading.Thread):
    """`week_fn` returns the current week payload (the API's cached /week); the play feed is
    derived for its starters. None (tests, a dev API) means no play feed."""

    def __init__(self, state: State = STATE, settings: dict | None = None,
                 week_fn: Callable[[], dict] | None = None):
        super().__init__(daemon=True, name="live-fastcast")
        self.state = state
        self.table = fc.scoring_table(settings or load())
        self.stop = threading.Event()
        self.pause = threading.Event()  # set by _on_doc to drop the socket until the next kickoff
        self.idle_until: float | None = None
        # errors surface on /live/status as they happen: a callback that raises on every doc
        # would otherwise read as "connected" while the client reconnects in a loop
        self.client = fc.Client(self._on_doc, on_error=lambda e: setattr(self.state, "error", e))
        self.week_fn = week_fn
        self.roster: dict[int, dict] = {}
        self.week: int | None = None
        self.roster_at = 0.0
        self.store: P.Store | None = None
        self.plays = P.PlayCache()

    def _refresh_roster(self) -> None:
        if self.week_fn is None or time.time() - self.roster_at < ROSTER_REFRESH:
            return
        self.roster_at = time.time()
        try:
            week = self.week_fn()
        except Exception as e:  # noqa: BLE001 — the feed keeps the last roster it had
            self.state.error = f"roster refresh failed: {e}"
            return
        self.roster = P.roster_index(week)
        if week.get("week") != self.week:
            self.week = week["week"]
            self.store = P.Store(P.week_path(self.week))

    def _on_doc(self, doc: dict) -> None:
        self._refresh_roster()
        before, first = dict(self.state.points), self.state.version == 0
        after = fc.score_all(doc, self.table)
        self.state.update(after, fc.game_states(doc), fc.player_stats(doc), week=self.week)
        self.plays.remember(doc)  # before the events: the play that moved a player is in this doc
        # the first checkpoint after boot is a baseline, not a burst of "events" for every player
        if self.store is not None and not first:
            self.store.add(P.events_from(before, after, doc, self.roster, datetime.now(), self.week, self.plays))
        idle = idle_for(doc)
        if idle is not None:
            self.idle_until = time.time() + idle
            self.pause.set()

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
