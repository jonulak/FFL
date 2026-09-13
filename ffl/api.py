"""FastAPI glue for the season UI: ESPN/Sleeper state behind a short cache, two writes.

    uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300

Loopback only; Next.js on :3300 proxies /api/* here. Stale payloads are kept past the TTL and
served, flagged, when a source is down — never a blank page on Sunday.

Only three endpoints write anything: POST /decisions appends to journal/DECISIONS.md, POST /refresh
runs the journal sync, and POST /outlook/refresh starts a background run that writes
data/outlook/week-<n>.json. Every GET is read-only.
"""
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, field_validator

from . import espn_season, live, outlook, season
from .config import JOURNAL
from .journal import sync_results, sync_transactions
from .settings import load

TTL = 120.0
DECISIONS = JOURNAL / "DECISIONS.md"


class Entry:
    def __init__(self, expires: float, built_at: float, payload: object, fn: Callable[[], object], ttl: float):
        self.expires, self.built_at, self.payload, self.fn, self.ttl = expires, built_at, payload, fn, ttl
        self.failed = False  # the latest build attempt raised: the payload is older than it should be


class Cache:
    """TTL cache that serves stale while it revalidates, and keeps expired entries so a dead
    source can still be answered.

    A cold ESPN build is one to four seconds, so a request never waits on one when there is any
    payload to hand back: an expired entry is served at once and rebuilt on a thread behind it,
    one thread per key however many requests arrive. Only the very first build of a key (and the
    Refresh button, which is the user asking to wait) is inline. `stale_since` is set only when
    the latest attempt failed — an entry that is merely past its TTL is not "source down".
    """

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._d: dict[str, Entry] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def get_or_build(self, key: str, fn: Callable[[], object], ttl: float | None = None):
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
        if hit and hit.expires > now:
            return hit.payload, self._stale(hit)
        if hit:
            self._revalidate(key)
            return hit.payload, self._stale(hit)
        try:
            payload = fn()
        except Exception as e:  # noqa: BLE001 — a first build with nothing to fall back on
            src = "sleeper" if "sleeper" in repr(e).lower() else "espn"
            raise HTTPException(status_code=502, detail={"source": src, "error": str(e)})
        with self._lock:
            self._d[key] = Entry(now + (ttl or self.ttl), now, payload, fn, ttl or self.ttl)
        return payload, None

    @staticmethod
    def _stale(hit: Entry) -> str | None:
        return datetime.fromtimestamp(hit.built_at).strftime("%H:%M") if hit.failed else None

    def _revalidate(self, key: str) -> None:
        with self._lock:
            t = self._threads.get(key)
            if t is not None and t.is_alive():
                return
            t = threading.Thread(target=self._rebuild, args=(key,), daemon=True, name=f"cache-{key}")
            self._threads[key] = t
        t.start()

    def _rebuild(self, key: str) -> None:
        """Build `key` again in place. A failure keeps the old payload and flags it."""
        with self._lock:
            hit = self._d.get(key)
        if hit is None:
            return
        try:
            payload = hit.fn()
        except Exception as e:  # noqa: BLE001 — any source failure keeps the stale payload
            print(f"cache rebuild of {key} failed: {e}")
            hit.failed = True
            return
        now = time.time()
        with self._lock:
            self._d[key] = Entry(now + hit.ttl, now, payload, hit.fn, hit.ttl)

    def rebuild_expired(self) -> None:
        """Every expired key rebuilt inline, in this thread. Failures are flagged, not raised."""
        with self._lock:
            keys = [k for k, e in self._d.items() if e.expires <= time.time()]
        for k in keys:
            self._rebuild(k)

    def join(self, timeout: float = 10.0) -> None:
        """Wait for every rebuild in flight (tests, and the startup warm-up)."""
        with self._lock:
            threads = list(self._threads.values())
        for t in threads:
            t.join(timeout)

    def has(self, key: str) -> bool:
        return key in self._d

    def expire_all(self):
        with self._lock:
            for e in self._d.values():
                e.expires = 0.0

    def clear(self):
        with self._lock:
            self._d.clear()


cache = Cache(TTL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The journal is the durable record: the API reads and appends to it, and never creates it.
    # Booting without it would mean a 500 on /journal and a silent new file on the first
    # decision, so refuse to start instead. Read the module global so tests can repoint it.
    if not DECISIONS.exists():
        raise RuntimeError(f"journal decisions log missing: {DECISIONS} (the API never creates it)")
    # Warm the page caches so the first browser hit after a (re)start is not the cold build.
    # Registered with the cache's thread table so join() covers it; a failure is logged and the
    # request path builds inline as it always did.
    t = threading.Thread(target=warm, daemon=True, name="cache-warm")
    cache._threads["warm"] = t
    t.start()
    # One FastCast subscription per process; overlays live points on the cached payloads and
    # wakes /live. FFL_LIVE=0 keeps it off (tests, a second dev API).
    runner = live.Runner(week_fn=lambda: cache.get_or_build("week:current", lambda: espn_season.week_state(None))[0]) \
        if live.enabled() else None
    if runner:
        runner.start()
    app.state.runner = runner
    yield
    if runner:
        runner.stop.set()


def warm() -> None:
    for key, fn in (("week:current", lambda: espn_season.week_state(None)),
                    ("roster", lambda: espn_season.roster_state(None)),
                    ("league:current", lambda: espn_season.league_state(None)),
                    ("waivers", lambda: espn_season.free_agent_pool(None))):
        try:
            cache.get_or_build(key, fn)
        except HTTPException as e:
            print(f"warm-up of {key} failed: {e.detail}")


app = FastAPI(title="FFL season API", lifespan=lifespan)


def _journal_serve(fn: Callable[[], dict]) -> dict:
    """Journal reads/writes are local files, but a missing or unreadable one must still come back
    in the shape the UI's error card expects, never as a bare 500 traceback."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — any journal failure is reported like a source failure
        raise HTTPException(status_code=502, detail={"source": "journal", "error": str(e)})


def _serve(key: str, fn: Callable[[], dict]) -> dict:
    payload, stale = cache.get_or_build(key, fn)
    return {**payload, "stale_since": stale}


@app.get("/health")
def health():
    return {"ok": True}


def _current_week() -> int | None:
    """The NFL week the live feed covers; None when ESPN is unreachable (no overlay then)."""
    try:
        return espn_season.fresh_league().current_week
    except Exception:  # noqa: BLE001 — the cached payload is still served, just without live points
        return None


def _live_points(week: int | None) -> dict[int, float]:
    """The live overlay for `week`: only the current week, only while the feed has scored something."""
    if not live.STATE.points:
        return {}
    if week is not None and week != _current_week():
        return {}
    return live.STATE.points


FEED_ROWS = 5


def _plays(limit: int | None = None) -> list[dict]:
    runner = getattr(app.state, "runner", None)
    store = runner.store if runner else None
    return store.latest(limit) if store else []


@app.get("/week")
def week_current():
    data = _serve("week:current", lambda: espn_season.week_state(None))
    # the feed rides on the page's own payload so one live tick refreshes both
    return {**live.overlay_week(data, _live_points(data["week"])), "plays": _plays(FEED_ROWS)}


@app.get("/plays")
def plays_():
    """Every play-feed row of the current week, newest first (the /plays page)."""
    runner = getattr(app.state, "runner", None)
    return {"week": runner.week if runner else None, "live": season.is_live(), "plays": _plays(),
            "stale_since": None}


@app.get("/week/{n}")
def week(n: int):
    data = _serve(f"week:{n}", lambda: espn_season.week_state(n))
    runner = getattr(app.state, "runner", None)
    plays = _plays(FEED_ROWS) if runner and runner.week == n else []
    return {**live.overlay_week(data, _live_points(data["week"])), "plays": plays}


@app.get("/live")
def live_stream():
    """Server-sent events: one `data: <version>` line whenever live points change, and a comment
    every 15 s so the proxy keeps the connection open. The page refetches its GET on a tick."""
    def gen():
        seen = live.STATE.version
        yield f"retry: 3000\ndata: {seen}\n\n"
        while True:
            v = live.STATE.wait_for_change(seen, timeout=15.0)
            if v != seen:
                seen = v
                yield f"data: {v}\n\n"
            else:
                yield ": keepalive\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             # no-transform: Next's compression would otherwise gzip the stream and hold it in a
                             # buffer the browser never sees a byte of
                             headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"})


@app.get("/live/status")
def live_status():
    return live.STATE.snapshot()


@app.get("/waivers")
def waivers(pos: Literal["QB", "RB", "WR", "TE", "D/ST", "K"] = Query(...)):
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    return {"week": data["week"], "pos": pos, "rows": data["positions"].get(pos, []),
            "stale_since": data["stale_since"]}


@app.get("/league")
def league_(week: int | None = None):
    data = _serve(f"league:{week or 'current'}", lambda: espn_season.league_state(week))
    return live.overlay_league(data, _live_points(data["week"]))


LOGO_TTL = 24 * 3600.0


@app.get("/logo/{team_id}")
def logo(team_id: int):
    """A fantasy team's logo, fetched with the league cookies (ESPN refuses an owner's upload
    without them) and held for a day. A missing or failed logo is a 404; the UI shows its tile."""
    try:
        body, ctype = cache.get_or_build(f"logo:{team_id}", lambda: espn_season.team_logo(team_id), ttl=LOGO_TTL)[0]
    except Exception as e:  # noqa: BLE001 — any failure means "no logo", never a traceback
        raise HTTPException(status_code=404, detail={"source": "espn", "error": str(e)})
    return Response(content=body, media_type=ctype, headers={"cache-control": f"private, max-age={int(LOGO_TTL)}"})


@app.get("/journal")
def journal_():
    return {**_journal_serve(espn_season.journal_state), "stale_since": None}


@app.get("/draft")
def draft_():
    return {**espn_season.draft_recap(), "stale_since": None}


@app.get("/roster")
def roster_():
    data = live.overlay_roster(_serve("roster", lambda: espn_season.roster_state(None)), _live_points(None))
    # attach on every request, not inside the cached build: a finished generation run must show
    # on the next load, not after the TTL
    return {**data, "cards": outlook.attach(data["cards"], outlook.load(data["week"])),
            "outlook_run": {**outlook.LAST, "running": outlook.running()}}


@app.post("/outlook/refresh", status_code=202)
def outlook_refresh():
    try:
        state = espn_season.roster_state(None)
    except Exception as e:  # noqa: BLE001 — a source failure is a 502, not a bare traceback
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    todo = outlook.pending(state["cards"], outlook.load(state["week"]))
    try:
        outlook.start(state["cards"], state["week"], run=outlook.run_claude)
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"started": True, "pending": len(todo)}


class Decision(BaseModel):
    title: str
    decision: str
    reasoning: str
    alternatives: str = ""

    @field_validator("title", "decision", "reasoning")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("required")
        return v


@app.post("/decisions")
def post_decision(d: Decision):
    text = _journal_serve(
        lambda: season.append_decision(DECISIONS, d.title, d.decision, d.reasoning, d.alternatives))
    return {"decisions_md": text}


def sync_all() -> dict:
    # espn_api caches current_week and the records in League.__init__; a months-old League would
    # make sync_results rewrite journal/results/weekly.csv truncated to the boot week.
    espn_season.refresh_league()
    lg = espn_season.fresh_league()
    settings = load()
    sync_transactions(lg)
    sync_results(lg, settings)
    return {"transactions": True, "results": True}


@app.post("/refresh")
def refresh():
    # expire_all, not clear: a refresh while ESPN is down must still leave the stale payloads
    # behind for the fallback to serve, flagged — never a blank page on Sunday.
    cache.expire_all()
    # the Sleeper-week, season-projection and injury caches live in espn_season; Refresh busts those too
    espn_season._SL.clear()
    espn_season._SEASON_CACHE.clear()
    espn_season._BOXES.clear()
    if espn_season._INJ:  # expire, not clear: _INJ is the last-good store the outage fallback serves
        espn_season._INJ[0] = 0.0
    try:
        synced = sync_all()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — an ESPN failure is a 502, not a bare traceback
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    # The button is the one place the user asks to wait: rebuild here so the page it re-renders
    # is fresh, rather than the old payload with a rebuild running behind it.
    cache.rebuild_expired()
    return {"synced": synced, "at": datetime.now().strftime("%H:%M")}
