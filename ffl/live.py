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
        self.at: float | None = None  # when the points last changed
        self.connected = False
        self.error: str | None = None
        self.cond = threading.Condition()

    def update(self, points: dict[int, float]) -> bool:
        with self.cond:
            if points == self.points and self.version:
                return False
            self.points, self.version, self.at = points, self.version + 1, time.time()
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
                    "error": self.error, "players": len(self.points)}


STATE = State()


def enabled() -> bool:
    """FFL_LIVE=0 keeps the socket closed (tests, a second dev API on the same machine)."""
    return os.environ.get("FFL_LIVE", "1") != "0"


# ---- overlay ----------------------------------------------------------------------------------

def overlay_players(players: list[dict], points: dict[int, float]) -> list[dict]:
    return [{**p, "actual": points[p["player_id"]]} if p.get("player_id") in points else p for p in players]


def overlay_week(payload: dict, points: dict[int, float]) -> dict:
    out = dict(payload)
    for key in ("me", "opp"):
        side = payload.get(key)
        if not side:
            continue
        side = {**side, **{g: overlay_players(side.get(g, []), points) for g in ("lineup", "bench", "ir")}}
        side["actual_total"] = round(sum(p["actual"] for p in side["lineup"]), 1)
        out[key] = side
    return out


def overlay_roster(payload: dict, points: dict[int, float]) -> dict:
    return {**payload, "cards": overlay_players(payload.get("cards", []), points)}


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
        self.client = fc.Client(self._on_doc)
        self.week_fn = week_fn
        self.roster: dict[int, dict] = {}
        self.week: int | None = None
        self.roster_at = 0.0
        self.store: P.Store | None = None

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
        self.state.update(after)
        # the first checkpoint after boot is a baseline, not a burst of "events" for every player
        if self.store is not None and not first:
            self.store.add(P.events_from(before, after, doc, self.roster, datetime.now(), self.week))
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
