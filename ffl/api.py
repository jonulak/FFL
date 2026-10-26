"""FastAPI glue for the season UI: ESPN/Sleeper state behind a short cache, a few writes.

    uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300

Loopback only; Next.js on :3300 proxies /api/* here. Stale payloads are kept past the TTL and
served, flagged, when a source is down — never a blank page on Sunday.

Only these endpoints write anything: POST /refresh runs the journal sync, POST /outlook/refresh starts a background run that writes
the week's outlooks to the DB, POST /waivers/outlook/{id} writes one free agent's the same way (kind waivers-week),
POST /claims saves the week's claim plan to the DB (never touches ESPN), and POST /claims/submit is
the one route that writes to ESPN (instant free-agent adds + waiver claims, through ffl/espn_write.py) and journals it;
POST /lineup writes to ESPN too (moves players between lineup slots, one transaction) and journals it.
POST /decisions/{id} gives a decision its reason and outcome; POST /decisions/prefs sets the Settings switches.
POST /notify/subscribe, /notify/unsubscribe and /notify/prefs write the DB's notify tables; POST /notify/tick syncs
the log and sends push notifications; POST /notify/test sends one. POST /news/run runs the news desk (ffl/newsdesk),
which writes the news tables and queues its pushes.
Every GET is read-only.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable, Literal

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import calibrate, db, espn_season, espn_write, leagues, live, nfl, notify, outlook, player_card, player_views, season, sources
from . import fastcast, news, playbook, watch
from . import claims as claims_mod
from . import db
from . import decisions as decisions_mod
from . import plays as plays_mod
from . import journal
from .flight import Flight
from .journal import sync_results, sync_transactions
from .newsdesk import feed as news_feed
from .newsdesk import pipeline
from .newsdesk import players as news_players
from .newsdesk import usage as news_usage
from .leagues import LeagueRef
from .settings import load

log = logging.getLogger(__name__)
TTL = 120.0


# The page keys a persisted Cache writes through to the DB's page_cache (by the part before the first ":"). Not the
# live runner's starters (a stale copy would side plays to the wrong team), logos (bytes) or the draft (not a page).
PERSIST = ("week", "roster", "league", "waivers", "team", "transactions", "player")
PLAYER_KEEP = 7 * 86400.0  # a stored player card older than this is pruned at load: one per player ever opened


class Entry:
    def __init__(self, expires: float, built_at: float, payload: object, fn: Callable[[], object] | None, ttl: float):
        # fn is None only on a page loaded from the DB at boot, until its first request hands it a builder
        self.expires, self.built_at, self.payload, self.fn, self.ttl = expires, built_at, payload, fn, ttl
        self.failed = False  # the latest build attempt raised: the payload is older than it should be


class Cache:
    """TTL cache that serves stale while it revalidates, and keeps expired entries so a dead
    source can still be answered.

    A cold ESPN build is one to four seconds, so a request never waits on one when there is any
    payload to hand back: an expired entry is served at once and rebuilt on a thread behind it,
    one thread per key however many requests arrive. Only the very first build of a key (and the
    Refresh button, which is the user asking to wait) is inline, and that one is shared: a caller
    arriving while it runs waits for it rather than building again (ffl.flight). `stale_since` is set only when
    the latest attempt failed — an entry that is merely past its TTL is not "source down".

    With `persist`, every installed page (PERSIST) is written through to the DB's page_cache, and `load()` at boot
    takes the rows back as expired entries: the first open after a restart is served at once and rebuilt behind,
    never cold. A drop deletes the rows too, so a page a league move dropped never comes back from the DB.
    """

    def __init__(self, ttl: float, persist: bool = False, ref=None):
        self.ttl = ttl
        self._d: dict[str, Entry] = {}
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}
        self._flight = Flight()  # a key's first build in flight, shared by its concurrent callers
        # bumped by drop_prefixes (a league move): a build that began before it read the rosters from before it,
        # so it hands its payload to its caller but never installs it
        self._gen = 0
        self._persist = persist
        self._ref = ref  # the league whose page rows this cache reads and writes; None is the home league
        # held (before _lock, never inside it) across each DB write and each drop's pop + delete, so a write for an
        # entry a drop has popped can never land after the drop's delete
        self._db_lock = threading.Lock()

    def get_or_build(self, key: str, fn: Callable[[], object], ttl: float | None = None):
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit and hit.fn is None:  # loaded from the DB: this request brings the builder
                hit.fn, hit.ttl = fn, ttl or self.ttl
        if hit and hit.expires > now:
            return hit.payload, self._stale(hit)
        if hit:
            stale = self._stale(hit)  # read before the rebuild starts: a thread that fails fast must not flag this reply
            self._revalidate(key)
            return hit.payload, stale
        return self._flight.do(key, lambda: self._first_build(key, fn, ttl))

    def _first_build(self, key: str, fn: Callable[[], object], ttl: float | None):
        """The inline build of a key with nothing cached, shared by every caller that arrives while it runs (warm()
        and the first open, a move's rewarm and the notice's link): one build, its payload or its 502 to each."""
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
        if hit and hit.fn is not None and hit.expires > now:  # the flight before this one installed it
            return hit.payload, self._stale(hit)
        gen = self._gen
        try:
            payload = fn()
        except Exception as e:  # noqa: BLE001 — a first build with nothing to fall back on
            src = "sleeper" if "sleeper" in repr(e).lower() else "espn"
            raise HTTPException(status_code=502, detail={"source": src, "error": str(e)})
        entry = Entry(now + (ttl or self.ttl), now, payload, fn, ttl or self.ttl)
        with self._lock:
            if self._gen == gen:
                self._d[key] = entry
        self._store(key, entry)
        return payload, None

    def load(self) -> None:
        """Take every stored page back as an expired entry (boot). Old player cards are pruned instead."""
        if not self._persist:
            return
        try:
            rows = db.pages(ref=self._ref)
        except (db.DBError, db.SchemaError) as e:
            print(f"page cache load failed: {e}")
            return
        old = [r["key"] for r in rows
               if r["key"].split(":", 1)[0] == "player" and r["built_at"] < time.time() - PLAYER_KEEP]
        with self._lock:
            for r in rows:
                if r["key"] not in old and r["key"] not in self._d:
                    self._d[r["key"]] = Entry(0.0, r["built_at"], r["payload"], None, self.ttl)
        with self._db_lock:
            self._unstore(old)  # housekeeping: a failure is logged and the load stands

    def _store(self, key: str, entry: Entry) -> None:
        """Write `entry` through to the DB if it is still the installed one. Only payloads that come back from JSON
        as they went in are stored: int keys or tuples would be served after a restart in another shape."""
        if not self._persist or key.split(":", 1)[0] not in PERSIST:
            return
        try:
            if json.loads(json.dumps(entry.payload)) != entry.payload:
                print(f"page cache: {key} does not survive JSON; not stored")
                return
        except (TypeError, ValueError) as e:
            print(f"page cache: {key} is not JSON ({e}); not stored")
            return
        with self._db_lock:
            with self._lock:
                if self._d.get(key) is not entry:
                    return  # dropped, or replaced by a newer install that writes its own row
            try:
                db.put_page(key, entry.payload, entry.built_at, ref=self._ref)
            except (db.DBError, db.SchemaError) as e:
                print(f"page cache write of {key} failed: {e}")

    def _unstore(self, keys: list[str] | None = None, prefixes: tuple[str, ...] = ()) -> None:
        """Delete these keys' rows, or every `prefix`/`prefix:...` row, or (neither) every row. Called holding
        _db_lock."""
        if not self._persist:
            return
        try:
            if keys is not None:
                db.drop_pages(keys, ref=self._ref)
            elif prefixes:
                db.drop_page_prefixes(prefixes, ref=self._ref)
            else:
                db.clear_pages(ref=self._ref)
        except (db.DBError, db.SchemaError) as e:
            print(f"page cache delete failed: {e}")

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
        if hit is None or hit.fn is None:
            return  # never built, or loaded from the DB and not yet asked for: its first request rebuilds it
        started, gen = time.time(), self._gen
        try:
            payload = hit.fn()
        except Exception as e:  # noqa: BLE001 — any source failure keeps the stale payload
            print(f"cache rebuild of {key} failed: {e}")
            hit.failed = True
            return
        now = time.time()
        with self._lock:
            if self._gen != gen:
                return  # a league move dropped the pages while this build ran; its read is from before it
            cur = self._d.get(key)
            if cur is not None and cur.built_at > started:
                return  # something was installed (put) while this build ran; its read is older
            entry = self._d[key] = Entry(now + hit.ttl, now, payload, hit.fn, hit.ttl)
        self._store(key, entry)

    def put(self, key: str, payload: object) -> None:
        """Install a payload built by the caller, fresh. A revalidate that began before this lands cannot replace it."""
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return
            entry = self._d[key] = Entry(now + hit.ttl, now, payload, hit.fn, hit.ttl)
        self._store(key, entry)

    def rebuild(self, *keys: str) -> None:
        """These keys rebuilt inline, in this thread (the ones never built are skipped). Failures are flagged, not raised."""
        for k in keys:
            self._rebuild(k)

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

    def peek(self, key: str) -> object | None:
        """The installed payload, fresh or not, without building it; None when nothing is cached."""
        with self._lock:
            hit = self._d.get(key)
        return hit.payload if hit else None

    def expire(self, prefix: str):
        """Mark `prefix` and every `prefix:...` key expired: served stale once, rebuilt behind the request."""
        with self._lock:
            for k, e in self._d.items():
                if k == prefix or k.startswith(prefix + ":"):
                    e.expires = 0.0

    def drop(self, key: str):
        """Forget `key`: the next request builds it inline, never served from the old payload."""
        with self._db_lock:
            with self._lock:
                self._d.pop(key, None)
            self._unstore([key])

    def drop_prefixes(self, prefixes: tuple[str, ...]) -> list[tuple[str, Callable[[], object], float]]:
        """Forget every key that is a prefix or `prefix:...`, and every such stored row (loaded or not); returns
        (key, builder, ttl) of each that has a builder, to rebuild."""
        with self._db_lock:
            with self._lock:
                self._gen += 1
                keys = [k for k in self._d if any(k == p or k.startswith(p + ":") for p in prefixes)]
                popped = [(k, self._d.pop(k)) for k in keys]
            self._unstore(prefixes=prefixes)
        return [(k, e.fn, e.ttl) for k, e in popped if e.fn is not None]

    def expire_all(self):
        with self._lock:
            for e in self._d.values():
                e.expires = 0.0

    def clear(self):
        with self._db_lock:
            with self._lock:
                self._d.clear()
            self._unstore(None)


cache = Cache(TTL, persist=True)
# One page cache per league, by registry key. Home's is the module `cache` (the rows with no league named, and the
# lifespan's thread table); any other league's is made on its first request and loaded from its own rows.
_CACHES = threading.Lock()
caches: dict[str, Cache] = {}


def cache_for(ref: LeagueRef | None) -> Cache:
    """The page cache of the league `ref` names; None is `cache` itself, with no registry read."""
    if ref is None:
        return cache
    with _CACHES:
        if ref.home:
            return caches.setdefault(ref.key, cache)
        c = caches.get(ref.key)
    if c is None:  # load reads the DB: never under _CACHES, which every home request takes
        c = Cache(TTL, persist=True, ref=ref)
        c.load()
        with _CACHES:
            c = caches.setdefault(ref.key, c)  # a duplicate that lost is dropped; load only reads
    return c


def open_league(league: str | None = Query(None)) -> LeagueRef:
    """The league a request names with ?league=<registry key>; none (or empty) is home. An unknown key is a 404."""
    if not league:
        return leagues.home()
    try:
        return leagues.get(league)
    except SystemExit:
        raise HTTPException(status_code=404, detail={"error": "unknown_league", "league": league}) from None


PROVIDERS = {"espn": "ESPN"}


def _league_format(ref: LeagueRef) -> str | None:
    """'2QB · 10 teams' for the switcher; None when the league's settings cannot be read."""
    try:
        s = espn_season.load(ref=ref)
        qbs = "2QB" if s["starters"].get("QB", 1) > 1 or "OP" in s["starters"] else "1QB"  # board.is_multi_qb
        return f"{qbs} · {s['team_count']} teams"
    except (Exception, SystemExit) as e:  # noqa: BLE001 — load exits on missing settings; the switcher still lists him
        log.warning("format of %s not read: %s", ref.key, e)
        return None


# The pages a league move makes stale: the log, every roster (mine, a team's, the boards'), the pool, the live
# starters, a player's card (owner, record). Logos, the draft and finished weeks' box scores never change.
MOVE_KEYS = ("transactions", "roster", "waivers", "week", "league", "team", "starters", "player")
# The pages a lineup move makes stale: every board and tile showing my starters, and the live feed's starters
LINEUP_KEYS = ("week", "league", "team", "starters")
REWARM_SKIP = ("player",)  # one card per player ever opened: built again on its next open, not all at once


def on_new_rows(rows: list[dict], ref: LeagueRef | None = None) -> None:
    """A sync logged a move in a league (home when `ref` is None), whichever path ran it (the tick, a page build, the
    reconcile). Drop every page of that league it makes stale: not expire, which serves the old page once while it
    rebuilds, so a move notice's link would open the page from before the move. Then rebuild them behind and tick
    /live's moves, so an open page refetches."""
    r = ref if ref is not None else leagues.home()
    espn_season.forget_current_boxes(ref=r)
    c = cache_for(r)
    _start_rewarm(c, c.drop_prefixes(MOVE_KEYS))


_REWARMS = itertools.count()


def _start_rewarm(c: Cache, dropped: list[tuple[str, Callable[[], object], float]]) -> None:
    """Rebuild the dropped pages on a thread, then tick /live's moves. Each move gets its own thread, all of them
    in the cache's thread table so join() waits for every one; a second move's drop stops the first's builds from
    installing (Cache._gen), and its own rewarm builds them again."""
    t = threading.Thread(target=_rewarm, args=(c, dropped), daemon=True, name="cache-moves")
    with c._lock:
        for k in [k for k, th in c._threads.items() if k.startswith("moves:") and not th.is_alive()]:
            del c._threads[k]
        c._threads[f"moves:{next(_REWARMS)}"] = t
    t.start()


def _rewarm(c: Cache, dropped: list[tuple[str, Callable[[], object], float]]) -> None:
    for key, fn, ttl in dropped:
        if key.split(":", 1)[0] in REWARM_SKIP:
            continue
        try:
            c.get_or_build(key, fn, ttl)
            if not c.has(key):  # it joined a build from before the drop, which hands out but never installs
                c.get_or_build(key, fn, ttl)
        except HTTPException as e:  # a source down: the page builds inline on its next open instead
            print(f"rebuild of {key} after a move failed: {e.detail}")
    live.STATE.moved()


journal.ON_NEW_ROWS.append(on_new_rows)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the page caches so the first browser hit after a (re)start is not the cold build.
    # Registered with the cache's thread table so join() covers it; a failure is logged and the
    # request path builds inline as it always did. The pages stored before the restart load first (expired), so
    # the warm-up and every first open serve them at once and rebuild behind.
    cache.load()
    t = threading.Thread(target=warm, daemon=True, name="cache-warm")
    cache._threads["warm"] = t
    t.start()
    # One FastCast subscription per process; overlays live points on the cached payloads and
    # wakes /live. FFL_LIVE=0 keeps it off (tests, a second dev API).
    runner = live.Runner(week_fn=lambda: cache.get_or_build("starters:current", lambda: espn_season.league_starters(None))[0],
                         leagues=live_leagues()) if live.enabled() else None
    if runner:
        runner.start()
    app.state.runner = runner
    yield
    if runner:
        runner.stop.set()


def _starters_fn(ref: LeagueRef) -> Callable[[], dict]:
    return lambda: cache_for(ref).get_or_build("starters:current", lambda: espn_season.league_starters(None, ref=ref))[0]


def live_leagues() -> list[tuple]:
    """Every league but home as the runner takes it: (ref, settings, starters). One whose settings cannot be read is
    left out (logged), never the reason home's feed does not start."""
    out = []
    for r in leagues.all_leagues():
        if r.home:
            continue
        try:
            out.append((r, load(r), _starters_fn(r)))
        except (Exception, SystemExit) as e:  # noqa: BLE001 — load exits on missing settings
            log.warning("live scoring off for %s: %s", r.key, e)
    return out


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


def _serve(key: str, fn: Callable[[], dict], ttl: float | None = None, *, ref: LeagueRef | None = None) -> dict:
    payload, stale = cache_for(ref).get_or_build(key, fn, ttl)
    return {**payload, "stale_since": stale}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/leagues")
def league_list():
    """Every registered league, home first: the switcher's rows (name · provider · format)."""
    return [{"key": r.key, "name": r.name, "provider": PROVIDERS.get(r.provider, r.provider), "format": _league_format(r),
             "home": r.home} for r in leagues.all_leagues()]


def _caps(ref: LeagueRef) -> dict:
    """The Settings page's caps table for a league: per position the league's limit, my cap and the cap the draft
    tools use (the lower). Read through strategy.caps, so a position with no cap of mine takes the preset's
    suggestion first, as the draft tools would."""
    from . import strategy
    strategy.caps(ref)
    rows, eff = db.caps_rows(ref.key), leagues.caps(ref)
    return {"league": ref.key, "name": ref.name, "rows": [{"pos": p, "provider_limit": rows.get(p, {}).get("provider_limit"),
                                         "my_cap": rows.get(p, {}).get("my_cap"), "cap": eff.get(p)}
                                        for p in leagues.CAP_POSITIONS]}


class NameIn(BaseModel):
    name: str


@app.post("/leagues/name")
def league_rename(body: NameIn, ref: LeagueRef = Depends(open_league)):
    """Rename the open league (League settings). 422 on a blank or overlong name."""
    try:
        r = leagues.rename(ref, body.name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"key": r.key, "name": r.name}


class CapIn(BaseModel):
    pos: str
    my_cap: int


@app.get("/caps")
def caps_get(ref: LeagueRef = Depends(open_league)):
    return _caps(ref)


@app.post("/caps")
def caps_set(body: CapIn, ref: LeagueRef = Depends(open_league)):
    """My cap at one position of the open league (`ffl.leagues caps`' rows): never above the league's limit (422)."""
    try:
        leagues.set_cap(ref, body.pos, body.my_cap)
    except SystemExit as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _caps(ref)


# --- waiver claim groups ("get K of N"): plan file, instant adds, ESPN claims with read-back ---

_SUBMIT = threading.Lock()  # one submit at a time: it cancels and reposts ESPN's whole claim list (home's; see _lock)
_LINEUP = threading.Lock()  # one move at a time: the answer is read back from ESPN, and two would read each other's half (home's)
_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[tuple[str, str], threading.Lock] = {}


def _lock(name: str, ref: LeagueRef) -> threading.Lock:
    """The write lock `name` ("lineup", "submit") of a league: one per league, so a move in one never 409s another's.
    Home's are the module's `_LINEUP` and `_SUBMIT`."""
    if ref.home:
        return {"lineup": _LINEUP, "submit": _SUBMIT}[name]
    with _LOCKS_GUARD:
        return _LOCKS.setdefault((name, ref.key), threading.Lock())


# token -> None while that submit runs, then its answer {status, body}. A phone that leaves the app mid-send loses
# the request ("Load failed") while the server finishes it; the dialog asks GET /claims/submit/{token} for the answer.
_SUBMITS: dict[str, dict | None] = {}
_SUBMIT_LEAGUE: dict[str, str] = {}  # token -> the league key whose send it was; another league's token is a 404
_SUBMITS_KEPT = 20


class GroupIn(BaseModel):
    targets: list[int]
    cuts: list[int]
    open: int = 0


class PlanIn(BaseModel):
    groups: list[GroupIn]


class MoveIn(BaseModel):
    group: int
    add: int
    drop: int | None = None


class SubmitIn(BaseModel):
    replace_outside: bool = False
    # the "Add now" list the dialog showed: Submit refuses (409) when the fresh statuses would add anything else
    expect_now: list[MoveIn] = []
    # the dialog's id for this send, so it can collect the answer if its own request drops
    token: str | None = None


def _instant_adds() -> bool:
    # Read at call time. On unless FFL_INSTANT_ADDS=0 (the FREEAGENT body was accepted on 2026-10-09, ffl-ut0.7).
    return os.environ.get("FFL_INSTANT_ADDS") != "0"


def _roster_size(*, ref: LeagueRef | None = None) -> int:
    return int(load(ref or leagues.home())["roster_size"])


def _limits(*, ref: LeagueRef | None = None) -> dict:
    return load(ref or leagues.home()).get("position_limits", {})


def _positions(data) -> dict:
    """player_id -> position over my roster, the pool and the other rosters: what the position caps are counted on."""
    rows = list(_rostered_rows(data).values()) + list(_pool_rows(data).values()) + data.get("roster", [])
    return {r["player_id"]: r.get("pos") for r in rows}


def _validate(plan, roster, data, status, *, ref: LeagueRef | None = None):
    return claims_mod.validate(plan["groups"], roster, set(_pool_rows(data)), _roster_size(ref=ref), status, _instant_adds(),
                               _owners(data), _positions(data), _limits(ref=ref))


def _load_plan(week, max_age=0, *, ref: LeagueRef | None = None):
    # a plan the waiver run already processed reads as retired (an empty week); the next save writes that down
    plan = db.claim_plan(week, ref=ref)
    if plan is None:
        return {"week": week, "groups": [], "submits": []}
    return claims_mod.retire(plan, espn_write.last_waiver_run(max_age, ref=ref))


def _save_plan(plan, *, ref: LeagueRef | None = None):
    plan["saved_at"] = datetime.now().isoformat(timespec="seconds")
    db.put_claim_plan(plan, ref=ref)


def _pool_rows(data):
    return {r["player_id"]: r for rows in data["positions"].values() for r in rows}


def _status(data):
    return {i: r.get("status") for i, r in _pool_rows(data).items() if r.get("status")}


def _fresh_status(data, *, ref: LeagueRef | None = None):
    """ESPN's statuses now (the pool is cached, and the Wednesday run turns WAIVERS into FREEAGENT); the cached
    ones when the read fails, where unknown counts as WAIVERS."""
    try:
        return {i: v["status"] for i, v in espn_season.pool_status(ref=ref).items() if v.get("status")}
    except Exception:  # noqa: BLE001
        return _status(data)


def _rostered_rows(data):
    """Every other team's players (the pool's `rostered` half): a target another team took keeps his name here."""
    return {r["player_id"]: r for rows in data.get("rostered", {}).values() for r in rows if not r.get("mine")}


def _owners(data):
    """player_id -> the name of the fantasy team that took him, for the claims' "taken by" error."""
    return {i: (r.get("owner") or {}).get("name") or (r.get("owner") or {}).get("abbrev") or "another team"
            for i, r in _rostered_rows(data).items()}


def _players(groups, data):
    # droppable None: the panel reads a non-null droppable as one of my own (its cut picker)
    rows = {i: {**r, "droppable": None, "locked": None} for i, r in _rostered_rows(data).items()} | _pool_rows(data)
    roster = {r["player_id"]: r for r in data.get("roster", [])}
    want = {i for g in groups for i in g["targets"] + g["cuts"]} | set(roster)
    # the schedule and injury fields are the player dialog's header (a claims-panel name opens it)
    keys = ("name", "pos", "team", "add", "ros_adj", "slot", "status", "waiver_date", "droppable", "locked",
            "opponent", "home", "kickoff", "bye", "injury", "injury_note", "return_week")
    return {str(i): {k: (rows.get(i) or roster.get(i)).get(k) for k in keys} for i in want if rows.get(i) or roster.get(i)}


def _claims_roster(data, *, ref: LeagueRef | None = None):
    """My roster as the claims read it: the cached pool's rows with the slots a fake lineup move (FFL_WRITES=0) put
    players in laid over, as the Roster page does, so a fake move off IR clears the IR block here too."""
    fake = espn_write.fake_slots(ref=ref)
    return [{**p, "slot": fake.get(p["player_id"], p.get("slot"))} for p in data.get("roster", [])]


# The Claims page's GET and save take ESPN's pending read when it is younger than this (any write drops it): a save
# changes nothing on ESPN, and each request paid two serial reads (1.5-2.3 s) before. Submit and fresh=1 read anew,
# once: the plan's retire check reads ESPN and the pending list takes that same read (JUST_READ_S).
CLAIMS_REUSE_S = 60
JUST_READ_S = 5


def _claims_payload(plan, data, status=None, max_age=CLAIMS_REUSE_S, *, ref: LeagueRef | None = None):
    roster, status = _claims_roster(data, ref=ref), _status(data) if status is None else status
    last = plan["submits"][-1] if plan["submits"] else None
    ids = set(last["ids"]) if last else set()
    now, expansion = claims_mod.plan_now(plan["groups"], status), claims_mod.expand(plan["groups"], status)
    try:
        pending, perr = espn_write.pending_state(max_age, ref=ref)[0], None
    except espn_write.WriteError as e:
        log.warning("pending claims not read: %s", e)  # the page shows only e.message; the cause is here
        pending, perr = [], e.message
    key = lambda ms: [(m["add"], m["drop"]) for m in ms]  # noqa: E731
    # ESPN holds the plan's claims, in order, once the claims filed on ESPN directly (kept outside) are set aside
    # and only when ESPN numbered them: an unnumbered claim (priority None) means its order is unknown
    on_espn = (bool(last) and perr is None and all(c.get("priority", 0) is not None for c in pending)
               and claims_mod.matches(expansion, [c for c in pending if c["id"] in ids]))
    return {"week": plan["week"], "groups": plan["groups"], "now": now, "claims": expansion,
            "errors": _validate(plan, roster, data, status, ref=ref),
            "warnings": claims_mod.cap_warnings(plan["groups"], roster, _positions(data), _limits(ref=ref)),
            "open_spots": claims_mod.open_spots(roster, _roster_size(ref=ref)),
            "position_caps": season.position_caps(roster, _limits(ref=ref)),
            "status": {str(i): s for i, s in status.items()},
            "pending": [{**c, "from_app": c["id"] in ids} for c in pending], "pending_error": perr,
            "last_submit": last,
            # claims only: instant adds already happened, and the plan's free agents are on the roster after a refresh
            "edited_since_submit": bool(last) and key(last["claims"]) != key(expansion),
            "on_espn": on_espn,
            # False under FFL_WRITES=0: every submit goes to the in-memory fake, and the UI must say so
            "writes": espn_write.writes(),
            "players": _players(plan["groups"], data)}


def _pool(ref: LeagueRef) -> dict:
    return _serve("waivers", lambda: espn_season.free_agent_pool(None, ref=ref), ref=ref)


@app.get("/claims")
def claims_get(fresh: bool = False, ref: LeagueRef = Depends(open_league)):
    # fresh=1 (the Submit dialog on open): statuses re-read from ESPN, so "Add now" is what Submit will run
    data = _pool(ref)
    if not fresh:
        return _claims_payload(_load_plan(data["week"], CLAIMS_REUSE_S, ref=ref), data, ref=ref)
    return _claims_payload(_load_plan(data["week"], ref=ref), data, _fresh_status(data, ref=ref), JUST_READ_S, ref=ref)


@app.post("/claims")
def claims_save(body: PlanIn, ref: LeagueRef = Depends(open_league)):
    # a save during a submit would overwrite its record (or be overwritten by it)
    lock = _lock("submit", ref)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a submit is running")
    try:
        data = _pool(ref)
        plan = {**_load_plan(data["week"], CLAIMS_REUSE_S, ref=ref), "groups": [g.model_dump() for g in body.groups]}
        _save_plan(plan, ref=ref)
        return _claims_payload(plan, data, ref=ref)
    finally:
        lock.release()


def _keep_submit(token, answer, ref: LeagueRef | None = None):
    _SUBMITS.pop(token, None)
    _SUBMITS[token] = answer
    _SUBMIT_LEAGUE[token] = (ref or leagues.home()).key
    while len(_SUBMITS) > _SUBMITS_KEPT:
        old = next(iter(_SUBMITS))
        _SUBMITS.pop(old)
        _SUBMIT_LEAGUE.pop(old, None)


@app.post("/claims/submit")
def claims_submit(body: SubmitIn, ref: LeagueRef = Depends(open_league)):
    if not body.token:
        return _claims_submit(body, ref)
    _keep_submit(body.token, None, ref)
    try:
        res = _claims_submit(body, ref)
    except HTTPException as e:
        _keep_submit(body.token, {"status": e.status_code, "body": {"detail": e.detail}}, ref)
        raise
    except Exception as e:
        _keep_submit(body.token, {"status": 500, "body": {"detail": str(e)}}, ref)
        raise
    _keep_submit(body.token, {"status": 200, "body": res}, ref)
    return res


@app.get("/claims/submit/{token}")
def claims_submit_answer(token: str, ref: LeagueRef = Depends(open_league)):
    """A send's answer by the dialog's token: running, or the status and body the POST answered (404: never arrived,
    or another league's)."""
    if token not in _SUBMITS or _SUBMIT_LEAGUE.get(token, leagues.home().key) != ref.key:
        raise HTTPException(404, "no submit with that token")
    answer = _SUBMITS[token]
    return {"state": "running"} if answer is None else {"state": "done", **answer}


def _claims_submit(body: SubmitIn, ref: LeagueRef):
    lock = _lock("submit", ref)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a submit is already running")
    try:
        data = _pool(ref)
        plan, roster = _load_plan(data["week"], ref=ref), _claims_roster(data, ref=ref)
        status = _fresh_status(data, ref=ref)
        # the dialog showed an "Add now" list from its own read: instant adds can't be undone, so any other is a 409
        now = claims_mod.plan_now(plan["groups"], status)
        if now != [m.model_dump() for m in body.expect_now]:
            raise HTTPException(409, {"error": "statuses changed — review again", "now": now})
        errs = _validate(plan, roster, data, status, ref=ref)
        if errs:
            raise HTTPException(422, {"errors": errs})
        try:
            pending, period = espn_write.pending_state(JUST_READ_S, ref=ref)
        except espn_write.WriteError as e:
            raise _espn_error(e)
        # ESPN's current scoring period, which is what its write bodies want; the plan's week is the cached pool's
        if period is not None and espn_season.league_period(period, ref=ref) != data["week"]:
            # as /lineup does: drop the cached League and pool, or the reload serves the same old week
            espn_season.refresh_league()
            cache_for(ref).drop("waivers")
            raise HTTPException(409, {"error": "ESPN's week changed — reload"})
        last = plan["submits"][-1] if plan["submits"] else None
        wk = data["week"] if period is None else period
        w = SimpleNamespace(pending_claims=lambda: espn_write.pending_claims(ref=ref),
                            cancel_claim=lambda i: espn_write.cancel_claim(i, wk, ref=ref),
                            submit_claim=lambda a, d: espn_write.submit_claim(a, d, wk, ref=ref),
                            add_free_agent=lambda a, d: espn_write.add_free_agent(a, d, wk, ref=ref),
                            reorder_claims=lambda ids: espn_write.reorder_claims(ids, ref=ref))
        res = claims_mod.run_submit(plan["groups"], status, pending, set(last["ids"]) if last else set(), body.replace_outside, w)
        strip = lambda m: {k: m[k] for k in ("group", "add", "drop")}  # noqa: E731
        # ids = what ESPN still holds of ours (sent now, or kept from the last submit when a cancel failed), so the
        # next submit cancels them; with no read-back, assume sent + last
        sent_ids, last_ids = [c["id"] for c in res["sent"]], list(last["ids"]) if last else []
        if res["readback_failed"]:
            ids = list(dict.fromkeys(sent_ids + last_ids))
        else:
            ids = [c["id"] for c in res["pending"] if c["id"] in sent_ids or c["id"] in last_ids]
        plan["submits"].append({"at": datetime.now().isoformat(timespec="seconds"), "now": [strip(m) for m in res["now"]],
                                "claims": [strip(c) for c in res["sent"]], "ids": ids, "match": res["match"]})
        submitted = plan["groups"]
        plan["groups"] = claims_mod.consume(submitted, res["now"])  # landed adds are on the roster now
        _save_plan(plan, ref=ref)
        decs = []
        if _records(ref=ref) and (res["now"] or res["sent"]):
            names = {i: r["name"] for i, r in _pool_rows(data).items()} | {r["player_id"]: r["name"] for r in roster}
            if res["sent"]:
                try:
                    decs.append(db.put_week_claims(data["week"], _mark_fake(claims_mod.decision_row(
                        submitted, names, status, data["week"], len(plan["submits"]), len(plan.get("retired", [])))), ref=ref))
                except db.DBError as e:
                    log.warning("claims not recorded: %s", e)
            decs += [r for r in (_record(_mark_fake(row), ref=ref) for row in claims_mod.free_add_rows(res["now"], names, data["week"])) if r]
        elif res["now"] or res["sent"]:
            log.info("claims not recorded (logging off, or FFL_WRITES=0 without FFL_RECORD_FAKE=1)")
        if res["now"]:
            # the roster changed: the pages that show it rebuilt inline, so the next one shows the add, not the dropped
            # player; the rest are served stale once and rebuilt behind it (all of them inline outlasts the client's 60 s)
            espn_season.forget_current_boxes(ref=ref)
            c = cache_for(ref)
            c.expire_all()
            c.rebuild("waivers", "roster", "week:current", "league:current")
        return {**res, "decisions": decs}
    finally:
        lock.release()


class LineupMove(BaseModel):
    player_id: int
    to_slot: str


class LineupIn(BaseModel):
    moves: list[LineupMove]


def _decision_prefs(*, ref: LeagueRef | None = None) -> dict:
    """The Settings switches; read after ESPN took a move, so a DB error falls back to the defaults, never a 500."""
    try:
        return decisions_mod.merge_prefs(db.get_league_state("decision_prefs", {}, ref=ref), {})
    except (db.DBError, ValueError) as e:
        log.warning("decision prefs unreadable, defaults used: %s", e)
        return dict(decisions_mod.DEFAULT_PREFS)


def _records(*, ref: LeagueRef | None = None) -> bool:
    """Real writes record their decisions unless logging is off in Settings; a dev API records only under
    FFL_RECORD_FAKE=1 (a worktree test server, own DB)."""
    return (espn_write.writes() or os.environ.get("FFL_RECORD_FAKE") == "1") and _decision_prefs(ref=ref)["log"]


def _mark_fake(row: dict) -> dict:
    return row if espn_write.writes() else {**row, "title": "[fake] " + row["title"], "facts": {**(row.get("facts") or {}), "fake": True}}


def _record(row: dict, *, ref: LeagueRef | None = None) -> dict | None:
    """Store a decision the app just made; the move stands whatever the DB says, so a failure is a warning and no ref."""
    try:
        return {"id": db.add_decision(row, ref=ref), "needs_rationale": row["rationale"] is None}
    except db.DBError as e:
        log.warning("decision not recorded (%s): %s", row["title"], e)
        return None


def _espn_error(e: espn_write.WriteError) -> HTTPException:
    log.warning("ESPN: %s", e)  # the page shows only e.message; the cause is here
    return HTTPException(502, {"source": "espn", "error": e.message})


@app.post("/lineup")
def lineup(body: LineupIn, ref: LeagueRef = Depends(open_league)):
    """Move players between ESPN slots (one transaction), then answer with the Roster page as ESPN now has it.
    Locks are re-read here, not trusted from the page: a game can kick off between the load and the tap."""
    c = cache_for(ref)  # before the acquire: an error here must not leave the lock held
    lock = _lock("lineup", ref)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a lineup move is already running")
    try:
        data = _serve("roster", lambda: espn_season.roster_state(None, ref=ref), ref=ref)
        # the two reads are independent: both in flight at once (0.45 s + 0.2 s, one after the other)
        with ThreadPoolExecutor(2) as pool:
            reading = pool.submit(lambda: espn_season.roster_flags(espn_season.fresh_league(ref=ref), ref=ref))
            pending = pool.submit(lambda: espn_write.pending_state(ref=ref))
        try:
            flags = reading.result()
            _, period = pending.result()
        except espn_write.WriteError as e:
            raise _espn_error(e)
        except Exception as e:  # noqa: BLE001 — the mRoster read
            raise HTTPException(502, {"source": "espn", "error": str(e)})
        # a league before its first week: ESPN's period is behind the page's week, which is the league's first
        if period is not None and espn_season.league_period(period, ref=ref) != data["week"]:
            # the League is cached up to LEAGUE_TTL_S: drop it and the page, or the Reload rebuilds the same old week
            espn_season.refresh_league()
            c.drop("roster")
            raise HTTPException(409, {"error": "ESPN's week changed — reload"})
        # slots and locks both from this one mRoster read: the cached page may be minutes behind
        # under FFL_WRITES=0 the page shows the fake store's slots, so the moves are validated against those ({} for real)
        roster = espn_season.with_espn_slots(data["cards"], flags, espn_write.fake_slots(ref=ref))
        items, errs = season.lineup_moves([m.model_dump() for m in body.moves], roster, espn_season.load(ref=ref))
        if errs:
            raise HTTPException(409, {"errors": errs})
        dec = None
        if items:
            uncertain = False
            try:
                espn_write.move_lineup(items, data["week"] if period is None else period, ref=ref)  # ESPN's period: what its body wants
            except espn_write.WriteError as e:
                raise _espn_error(e)
            except requests.RequestException:  # ESPN may have executed it before the connection died
                uncertain = True
            # recorded the moment ESPN may have taken the move, whatever the read-back does; a fake move
            # (FFL_WRITES=0) is recorded only under FFL_RECORD_FAKE=1, and nothing with logging off (_records)
            entry = season.lineup_entry(items, data["cards"], data["week"])
            if uncertain:
                entry["facts"]["uncertain"] = True
                entry["decision"] += " — ESPN did not answer; the move may not have happened, check ESPN"
            if _records(ref=ref):
                dec = _record(_mark_fake(entry), ref=ref)
            else:
                log.info("lineup move not recorded (logging off, or FFL_WRITES=0 without FFL_RECORD_FAKE=1): %s", entry["title"])
            # the moved players keep the rows they took (season.reseat), before the read-back reads the seats
            try:
                db.put_seats(season.reseat(items, data["cards"], db.seat_map(ref=ref)), ref=ref)
            except db.DBError as e:
                log.warning("lineup moved but its rows not kept: %s", e)
            espn_season.forget_current_boxes(ref=ref)  # the box scores carry the slots; read them again
            # the Matchup board, the League tile and my Team page show this lineup too: dropped, not expired, which
            # would serve the pre-move board once more; rebuilt behind, and /live ticks so an open board refetches
            _start_rewarm(c, c.drop_prefixes(LINEUP_KEYS))
            # the claims validate against the pool's roster slots (an IR player with no designation blocks every
            # claim): the next /claims or /players builds it from the moved roster, never from the pre-move one
            c.drop("waivers")
            if uncertain:
                c.drop("roster")
                raise HTTPException(502, {"source": "espn", "error": "ESPN did not answer — it may have moved; reload", "moved": True})
            # the read-back reuses this request's mRoster read, the moves applied (ESPN said EXECUTED): a second
            # read would only say the same; locks cannot have changed in the second since
            moved = {**flags, **{p: {**flags.get(p, {}), "slot": to} for p, _, to in items}}
            try:
                c.put("roster", espn_season.roster_state(None, flags=moved, ref=ref))
            except Exception as e:  # noqa: BLE001 — ESPN took the move; say so, never show the old slots as current
                c.drop("roster")  # the reload this asks for must build from ESPN, not serve the pre-move page
                raise HTTPException(502, {"source": "espn", "error": "moved in ESPN — the read-back failed, reload", "moved": True}) from e
        return {**roster_(ref), "decision": dec}
    finally:
        lock.release()


class SitIn(BaseModel):
    sat: bool


@app.post("/roster/sit/{player_id:int}")
def roster_sit(player_id: int, body: SitIn, ref: LeagueRef = Depends(open_league)):
    """Sit him for the week, or stand him back up: the call is re-made without him (season.roster_cards) and the
    page answers at once. Nothing reaches ESPN. Under the lineup lock, so a move's read-back and a sit never land
    over each other."""
    c = cache_for(ref)  # before the acquire: an error here must not leave the lock held
    lock = _lock("lineup", ref)
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "a lineup move is running")
    try:
        data = _serve("roster", lambda: espn_season.roster_state(None, ref=ref), ref=ref)
        before = next((card for card in data["cards"] if card["player_id"] == player_id), None)
        if before is None:
            raise HTTPException(404, f"player {player_id} is not on the roster")
        week = data["week"]
        dec = None
        if before.get("sat") != body.sat:
            try:
                db.put_sit(week, player_id, body.sat, ref=ref)
            except db.DBError as e:
                raise HTTPException(500, f"sit not saved: {e}") from e
            # a fake sit (FFL_WRITES=0) is recorded only under FFL_RECORD_FAKE=1, and nothing with logging off (_records)
            entry = season.sit_entry(before, body.sat, week)
            if _records(ref=ref):
                dec = _record(_mark_fake(entry), ref=ref)
            else:
                log.info("sit not recorded (logging off, or FFL_WRITES=0 without FFL_RECORD_FAKE=1): %s", entry["title"])
        # the cards carry every player field, so the call is re-made from them without an ESPN read
        sat = db.sat_ids(week, ref=ref)
        rows = espn_season.with_sits(data["cards"], sat)
        payload = {k: v for k, v in data.items() if k != "stale_since"}
        c.put("roster", {**payload, "cards": season.roster_cards(rows, espn_season.load(ref=ref), week)})
        if before.get("sat") != body.sat:  # the Matchup board's by-projection lineup leaves him out too (as /lineup)
            _start_rewarm(c, c.drop_prefixes(("week",)))
        return {**roster_(ref), "decision": dec}
    finally:
        lock.release()


# --- the watchlist (ffl/watch.py): the star, the Watch page, and the tick's hourly pass ---

WATCH_SINCE = "2000"  # every signal of the season: an unlit chip still names his last one of its kind


class WatchIn(BaseModel):
    watched: bool


def _watched(data: dict, *, ref: LeagueRef | None = None) -> list[dict]:
    """The pool's watched rows still watched now (an unwatch does not rebuild the pool). One now on my roster stops
    being watched: the Watch page is for players I do not have."""
    ids = set(db.watched_ids(ref=ref))
    rows = []
    for r in data.get("watched", []):
        if r["player_id"] not in ids:
            continue
        if r.get("mine"):
            db.put_watch(r["player_id"], False, ref=ref)
        else:
            rows.append(r)
    return rows


WATCH_BASELINES = ("above", "bench", "owner")


def _prune_watch_state(ids: set[int], st: dict | None = None, *, ref: LeagueRef | None = None) -> dict:
    """The pass's baselines kept only for the watched ids, written back and returned: an unwatched player's old
    owner would read as a drop when he is watched again. By the watchlist, never by the rows a pass priced: a read
    that missed a watched player keeps his baselines."""
    keep = {str(i) for i in ids}

    def prune(cur):
        out = dict(st if st is not None else (cur or {}))
        for f in WATCH_BASELINES:
            out[f] = {k: v for k, v in (out.get(f) or {}).items() if k in keep}
        return out
    return db.update_league_state("watch_state", prune, {}, ref=ref)


@app.get("/watch/ids")
def watch_ids(ref: LeagueRef = Depends(open_league)):
    return {"ids": db.watched_ids(ref=ref)}


@app.post("/watch/{player_id:int}")
def watch_set(player_id: int, body: WatchIn, ref: LeagueRef = Depends(open_league)):
    """Watch him or stop. Nothing reaches ESPN and nothing is a decision. A newly watched player the cached pool
    has no row for (a free agent deeper than its lists) rebuilds it behind the next request."""
    was = player_id in db.watched_ids(ref=ref)
    db.put_watch(player_id, body.watched, ref=ref)
    if not body.watched:
        _prune_watch_state(set(db.watched_ids(ref=ref)), ref=ref)
    if body.watched and not was:
        c = cache_for(ref)
        cached = c.peek("waivers")
        if cached is not None and player_id not in {r["player_id"] for r in cached.get("watched", [])}:
            c.expire("waivers")
    return {"watched": body.watched, "ids": db.watched_ids(ref=ref)}


@app.get("/watch")
def watch_page(ref: LeagueRef = Depends(open_league)):
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None, ref=ref), ref=ref)
    groups = watch.page(_watched(data, ref=ref), db.watch_signals(WATCH_SINCE, ref=ref), datetime.now().astimezone())
    return {"week": data["week"], "ros_week": data.get("ros_week", data["week"]), "groups": groups,
            "ids": db.watched_ids(ref=ref), "stale_since": data["stale_since"]}


def _key(ref: LeagueRef | None) -> str | None:
    """The live state's name for a league: None is home (live.State.points), any other its registry key."""
    return None if ref is None or ref.home else ref.key


def _live_for(week: int | None, ref: LeagueRef | None = None) -> bool:
    """Whether the feed's stored points belong to `week`. The state is tagged with the week the
    runner scored (live.State.week, a league's own in week_for); after Monday night it keeps last week's totals
    until the next kickoff, while the week payload has already rolled over, so a plain "is this the
    current week" check would lay last week's points on this week's zeros. A league before its first week pages
    a week the feed has not reached, so it gets nothing either."""
    return week is None or week == live.STATE.week_for(_key(ref))


def _live_points(week: int | None, ref: LeagueRef | None = None) -> dict[int, float]:
    """The league's live overlay for `week`: only the week the feed scored, only once it has scored something."""
    points = live.STATE.points_for(_key(ref))
    if not points or not _live_for(week, ref):
        return {}
    return points


def _live_games(week: int | None, ref: LeagueRef | None = None) -> dict[str, dict]:
    """Game clocks for the overlay (the NFL's, one for every league), gated like the league's points."""
    if not live.STATE.games or not _live_for(week, ref):
        return {}
    return live.STATE.games


def _live_stats(week: int | None, ref: LeagueRef | None = None) -> dict[int, dict]:
    """Raw stat rows for the overlay's stat lines, gated like the league's points."""
    if not live.STATE.stats or not _live_for(week, ref):
        return {}
    return live.STATE.stats


def _scoring(*, ref: LeagueRef | None = None) -> list[dict] | None:
    """The league's stat table for the overlay's scoring breakdown; None (no breakdown) when the
    settings file cannot be read, rather than failing the page."""
    try:
        return live.box_items(load(ref or leagues.home()))
    except (Exception, SystemExit):  # noqa: BLE001 — load() exits when the file is missing
        return None


def _playoff_weeks(*, ref: LeagueRef | None = None) -> list[int]:
    """The fantasy playoffs' NFL weeks, for the NFL tab's flags; none when the settings file cannot be read."""
    try:
        return load(ref or leagues.home()).get("playoff_weeks", [])
    except (Exception, SystemExit):  # noqa: BLE001 — load() exits when the file is missing
        return []


def _sigma() -> dict:
    """The win meter's spread per position (the fallback table when calibrate has not fitted one)."""
    return calibrate.load_sigma()[0]


FEED_ROWS = 5


def _plays(data: dict, limit: int | None = None, *, ref: LeagueRef | None = None) -> list[dict]:
    """The feed rows of one board's two teams, newest first, sided for that board. The runner's store is the
    week it last scored (after Tuesday's rollover still last week's); any other week reads its stored rows,
    the in-game plays it recorded and any stat correction after it."""
    sides = [s for s in (data.get("me"), data.get("opp")) if s]
    if not sides:
        return []
    teams = {s.get("team_id") for s in sides}
    try:
        store = _play_store(data.get("week"), ref=ref)
    except db.DBError as e:
        log.warning("plays for week %s not read: %s", data.get("week"), e)
        return []
    return plays_mod.for_board(store.latest(limit, teams=teams), sides[0].get("team_id"))


def _lane(ref: LeagueRef | None = None):
    """The runner's lane of a league (week, store, roster, table); home's is the runner itself. None: no runner,
    or one that does not score that league."""
    runner = getattr(app.state, "runner", None)
    if runner is None or _key(ref) is None:
        return runner
    lane = getattr(runner, "lane", None)
    return lane(ref.key) if lane else None


def _play_store(week: int, *, ref: LeagueRef | None = None) -> plays_mod.Store:
    """The runner's store of the league when it holds `week`, else that week's rows, plays and called-back rows
    from the DB."""
    lane = _lane(ref)
    if lane is not None and lane.store is not None and lane.week == week:
        return lane.store
    return plays_mod.Store(week, ref=None if _key(ref) is None else ref)


def _record_plays(week: int, entries: list[dict], *, ref: LeagueRef | None = None) -> int:
    """NFL plays read off ESPN's drives into a league's `week` feed store, with that week's starters; the number
    of called-back rows the week holds after."""
    lane = _lane(ref)
    live_week = lane is not None and lane.week == week and lane.roster
    roster = lane.roster if live_week else plays_mod.roster_index(espn_season.league_starters(week, ref=ref))
    table = lane.table if live_week else fastcast.scoring_table(load(ref or leagues.home()))
    store = _play_store(week, ref=ref)
    store.record(entries, roster, table)
    return len(store.called)


FINAL_PLAYS_AFTER = 30 * 60  # ESPN's drives can lag the final whistle: a game is read this long after it is seen final
_FINAL_SEEN: dict[str, float] = {}  # event id -> when the tick first saw it final


def final_plays() -> dict:
    """The tick's step: each finished game of the NFL tab's week and the one before whose plays ESPN's drives have
    not written yet, read once it has been final FINAL_PLAYS_AFTER, so every game's plays (and the plays called back
    in it) are ESPN's final word whether or not anyone opened it on the NFL tab. Into every league's feed, each
    league keeping its own record of the games read; a game's drives are fetched once for all of them."""
    shown = nfl.scoreboard(None)[0]["week"]
    out: dict[str, int] = {}
    # its own record of the games read: a game opened on the NFL tab mid-game has ESPN rows for what had been
    # played, not ESPN's final word
    done = {r.key: set(db.get_league_state("final_plays_done", [], ref=r)) for r in leagues.all_leagues()}
    refs = {r.key: r for r in leagues.all_leagues()}
    for week in (shown - 1, shown):
        if week < 1:
            continue
        now, due = time.time(), []
        for g in nfl.scoreboard(week)[0]["games"]:
            if g["status"] == "post" and any(g["event_id"] not in d for d in done.values()) \
                    and now - _FINAL_SEEN.setdefault(g["event_id"], now) >= FINAL_PLAYS_AFTER:
                due.append(g)
        entries: dict[str, list[dict]] = {}
        for g in due:
            clubs = (g["away"]["abbr"], g["home"]["abbr"])
            drives, _ = nfl.drives(g["event_id"], g["status"], {g["away"]["id"]: clubs[0], g["home"]["id"]: clubs[1]})
            entries[g["event_id"]] = playbook.from_drives(drives, g["event_id"], week, clubs)
        for key, d in done.items():
            games = [g for g in due if g["event_id"] not in d]
            plays_ = [e for g in games for e in entries[g["event_id"]]]
            if not plays_:
                continue
            _record_plays(week, plays_, ref=refs[key])
            out[str(week)] = max(out.get(str(week), 0), len(games))
            d |= {g["event_id"] for g in games}
            db.update_league_state("final_plays_done", lambda cur, d=d: sorted(set(cur or []) | d), [], ref=refs[key])
    return out


def _week_payload(data: dict, ref: LeagueRef | None = None) -> dict:
    """A board with the league's live points laid on and its feed riding along, so one live tick refreshes both."""
    wk = data["week"]
    return {**live.overlay_week(data, _live_points(wk, ref), _live_games(wk, ref), _live_stats(wk, ref),
                                _scoring(ref=ref), _sigma()),
            "plays": _plays(data, FEED_ROWS, ref=ref)}


def _board(n: int | None, team: int | None, *, ref: LeagueRef | None = None) -> dict:
    """My week's board, or with `team` the board of that team's game; each cached under its own key."""
    if team is None:
        return _serve("week:current" if n is None else f"week:{n}", lambda: espn_season.week_state(n, ref=ref), ref=ref)
    return _serve(f"week:{n or 'current'}:{team}", lambda: espn_season.week_state(n, team=team, ref=ref), ref=ref)


@app.get("/week")
def week_current(ref: LeagueRef = Depends(open_league)):
    return _week_payload(_board(None, None, ref=ref), ref)


@app.get("/plays")
def plays_(team: int | None = None, week: int | None = None, ref: LeagueRef = Depends(open_league)):
    """Every feed row of one game's week, newest first (the /plays page): mine, or with `team` that team's;
    `week` (a finished week's board links its own) else the current one. `me`/`opp` name the two sides the
    way the board does; `current` says whether the week is the current one (the way back is then `/`)."""
    runner = _lane(ref)
    current = _board(None, None, ref=ref)
    if team is None:
        data = current if week in (None, current["week"]) else _board(week, None, ref=ref)
    else:
        data = _board(week or (runner.week if runner and runner.week else None), team, ref=ref)
    club = lambda s: {k: s.get(k) for k in ("team_id", "team", "abbrev")} if s else None  # noqa: E731
    return {"week": data["week"], "current": data["week"] == current["week"], "live": season.is_live(),
            "mine": data.get("mine", True),
            "game_on": live.game_on([data.get("me"), data.get("opp")], _live_games(data["week"], ref)),
            "me": club(data.get("me")), "opp": club(data.get("opp")), "plays": _plays(data, ref=ref),
            "stale_since": data.get("stale_since")}


@app.get("/week/{n}")
def week(n: int, team: int | None = None, ref: LeagueRef = Depends(open_league)):
    """My week's board, or with `team` the board of that team's game (a League scoreboard tile)."""
    return _week_payload(_board(n, team, ref=ref), ref)


@app.get("/team/{team_id}")
def team(team_id: int, ref: LeagueRef = Depends(open_league)):
    """Any fantasy team's roster for the current week (the team page), the league's live points laid on."""
    data = _serve(f"team:{team_id}", lambda: espn_season.team_state(team_id, ref=ref), ref=ref)
    wk = data["week"]
    return {**data, "side": live.overlay_side(data["side"], _live_points(wk, ref), _live_games(wk, ref),
                                              _live_stats(wk, ref), _scoring(ref=ref))}


def _nfl_board(week: int, *, ref: LeagueRef | None = None) -> dict | None:
    """My Matchup board in the open league for `week`, live points laid on, for the NFL pages. The board failing
    (ESPN fantasy down) answers None: the pages go unmarked, never unserved."""
    try:
        data = _week_payload(_board(None, None, ref=ref), ref)
        if data["week"] != week:
            data = _week_payload(_board(week, None, ref=ref), ref)
    except HTTPException as e:
        log.warning("nfl marks off, no board for week %s: %s", week, e.detail)
        return None
    except SystemExit as e:  # config.league() exits when .env is missing; the NFL pages need no ESPN login
        log.warning("nfl marks off, no league config for week %s: %s", week, e)
        return None
    return data


def _nfl_sides(week: int, *, ref: LeagueRef | None = None) -> tuple[list[dict], list[dict]] | None:
    """My starters and my opponent's for `week` (the Matchup board's), for the NFL pages' marks. None when the
    board failed, and the game page says so rather than reading as "none of mine played"."""
    data = _nfl_board(week, ref=ref)
    if data is None:
        return None
    return (data.get("me") or {}).get("lineup", []), (data.get("opp") or {}).get("lineup", [])


def _nfl_teams(week: int, *, ref: LeagueRef | None = None) -> dict | None:
    """The two fantasy teams of my game in `week` (id, name, owner, mark, record), heading the game page's In this game table;
    `opp` None on a bye, the whole None when the board failed."""
    data = _nfl_board(week, ref=ref)
    if data is None:
        return None
    keys = ("team_id", "team", "owner", "abbrev", "logo", "wins", "losses", "ties")
    club = lambda s: {k: s.get(k) for k in keys} if s else None  # noqa: E731
    return {"me": club(data.get("me")), "opp": club(data.get("opp"))}


def _nfl_starters(week: int, *, ref: LeagueRef | None = None) -> tuple[list[dict], list[dict]]:
    """My starters and my opponent's for the scoreboard's per-club counts, which need only each one's club. The
    Matchup board's week reads its cached board; any other week reads the box scores' starters (league_starters,
    about a second cold) rather than pricing that week's board, which is 7-10 s cold. Failing, the scoreboard
    goes unmarked, never unserved."""
    try:
        if _board(None, None, ref=ref)["week"] == week:
            return _nfl_sides(week, ref=ref) or ([], [])
        me = espn_season.my_team_id(espn_season.fresh_league(ref), ref)
        for game in espn_season.league_starters(week, ref=ref)["games"]:
            if any(s["team_id"] == me for s in game):
                return (next(s["lineup"] for s in game if s["team_id"] == me),
                        next((s["lineup"] for s in game if s["team_id"] != me), []))
        return [], []
    except HTTPException as e:
        log.warning("nfl marks off, no board for week %s: %s", week, e.detail)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — ESPN fantasy down, or no league config
        log.warning("nfl marks off, no starters for week %s: %s", week, e)
    return [], []


def _nfl_scoreboard(week: int | None) -> tuple[dict, str | None]:
    try:
        return nfl.scoreboard(week)
    except Exception as e:  # noqa: BLE001 — a cold outage, nothing cached to serve
        raise HTTPException(502, {"source": "espn-nfl", "error": str(e)})



NFL_WEEKS = 18  # the NFL tab's week picker: the league's regular season is 18 games, not the fantasy 14


def _by_club(players: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in players:
        out[p["team"]] = out.get(p["team"], 0) + 1
    return out


@app.get("/nfl")
def nfl_scoreboard(week: int | None = None, ref: LeagueRef = Depends(open_league)):
    """Every NFL game of the week, live first then by kickoff window, my games first in each (the NFL tab)."""
    sb, stale = _nfl_scoreboard(week)
    if week is None:
        current = sb["week"]
    else:
        try:
            current = _nfl_scoreboard(None)[0]["week"]
        except HTTPException:  # the picker's "this week" mark is not worth failing the requested week
            current = sb["week"]
    # ESPN's page names its week; when it does not, the week asked for. With neither, no week is known and the
    # live gate would pass anything for None, so the feed's clocks stay off (another week's must not land on these
    # games) and so do the marks.
    wk = sb["week"] or week
    games = nfl.overlay(sb["games"], _live_games(wk)) if wk is not None else sb["games"]
    mine, opp = _nfl_starters(wk, ref=ref) if wk is not None else ([], [])
    return {"week": wk, "current": current, "weeks": NFL_WEEKS, "playoff_weeks": _playoff_weeks(),
            "live": any(nfl.is_live(g) for g in games),
            "sections": nfl.board(games, _by_club(mine), _by_club(opp)), "byes": sb["byes"], "stale_since": stale}


@app.get("/nfl/{week}/{team}")
def nfl_game(week: int, team: str, ref: LeagueRef = Depends(open_league)):
    """One game, by either club: its header, the starters of mine and my opponent's in it, its scoring plays and
    every drive, plays marked for those starters. 404 for an unknown club or a bye."""
    abbr = nfl.club(team)
    if abbr not in nfl.CLUBS:
        raise HTTPException(404, f"no club {team}")
    sb, stale = _nfl_scoreboard(week)
    game = next((g for g in sb["games"] if abbr in (g["away"]["abbr"], g["home"]["abbr"])), None)
    if game is None:
        raise HTTPException(404, f"no game for {abbr} in week {week}")
    game = nfl.overlay([game], _live_games(week))[0]
    clubs = (game["away"]["abbr"], game["home"]["abbr"])
    ids = {game["away"]["id"]: clubs[0], game["home"]["id"]: clubs[1]}
    try:
        drives, stale2 = nfl.drives(game["event_id"], game["status"], ids)
    except Exception as e:  # noqa: BLE001 — cold outage on this game's feed
        raise HTTPException(502, {"source": "espn-nfl", "error": str(e)})
    runner = getattr(app.state, "runner", None)
    # another week, or no starters read yet, waits for the tick: this page polls every 15 s. Every league the
    # runner scores that week gets the plays.
    if runner is not None and runner.store is not None and runner.week == week and runner.roster:
        entries = playbook.from_drives(drives, game["event_id"], week, clubs)
        for ln in getattr(runner, "lanes", [runner]):
            if ln.store is None or ln.week != week or not ln.roster:
                continue
            try:
                ln.store.record(entries, ln.roster, ln.table)
            except Exception as e:  # noqa: BLE001 — the page never fails on the play store
                log.warning("plays of %s not recorded for %s: %s", game["event_id"], getattr(ln, "key", None), e)
    sides = _nfl_sides(week, ref=ref)
    mine, opp = ([p for p in side if p.get("team") in clubs] for side in (sides or ([], [])))
    named = lambda ps: {p["player_id"]: p["name"] for p in ps if p["player_id"] > 0}  # noqa: E731
    dst = lambda ps: {p["team"]: p["name"] for p in ps if p["player_id"] < 0}  # noqa: E731
    marked = nfl.mark(drives, named(mine), named(opp), dst(mine), dst(opp), clubs)
    return {"week": week, "live": nfl.is_live(game),
            "game": {**game, "mine": len(mine), "opp": len(opp)},
            "marked": sides is not None,  # False: the Matchup board failed, so no play could be marked
            "starters": {"me": mine, "opp": opp},  # whole board players: a tap opens the Matchup breakdown
            "teams": _nfl_teams(week, ref=ref),
            "scoring": nfl.scoring_plays(marked), "drives": marked, "stale_since": stale or stale2}


PLAYER_TTL = 600.0  # a game log moves once a week; the live week rides on the feed, not this cache


@app.get("/player/{player_id}")
def player(player_id: int, ref: LeagueRef = Depends(open_league)):
    """One player's game log, this season and last (the season modal), the live week from the feed (home's league only)."""
    data = _serve(f"player:{player_id}", lambda: espn_season.player_card(player_id, ref=ref), PLAYER_TTL, ref=ref)
    if not ref.home:
        return data
    wk = data["current_week"]
    row, scoring = _live_stats(wk).get(player_id), _scoring()
    if row and scoring:
        data = player_card.with_live(data, wk, row, scoring, _live_games(wk).get(data["team"]))
    return data


@app.get("/live")
def live_stream():
    """Server-sent events: `data: <version>` whenever live points change, `data: m<n>` when a league move was
    logged (its pages already rebuilt), and a comment every 15 s so the proxy keeps the connection open. A live
    page refetches its GET on either; Transactions and Players refresh on a move only."""
    def gen():
        seen = (live.STATE.version, live.STATE.moves)
        yield live.stream_open(seen)
        while True:
            cur = live.STATE.wait_for_tick(seen, timeout=15.0)
            lines = live.stream_lines(seen, cur)
            seen = cur
            yield from lines or [": keepalive\n\n"]
    return StreamingResponse(gen(), media_type="text/event-stream",
                             # no-transform: Next's compression would otherwise gzip the stream and hold it in a
                             # buffer the browser never sees a byte of
                             headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"})


@app.get("/live/status")
def live_status():
    return live.STATE.snapshot()


@app.get("/sources/status")
def sources_status():
    """Each projection source's health: last success, rows per position, the error while failing
    (this process's view over the last one saved, so a fresh restart still shows yesterday's)."""
    return sources.on_only({**(db.get_state("source_status", {}) or {}), **sources.status_snapshot()})


_PRESETS = threading.Lock()  # one preset write at a time: each reads the store, changes it, writes it back


def _player_presets(*, ref: LeagueRef | None = None) -> dict:
    """The Players table's views: every league's (0024), whichever league the page is open on."""
    return player_views.load(db.get_state("player_presets"))


@app.get("/players")
def players(pos: Literal["QB", "RB", "WR", "TE", "D/ST", "K", "FLEX", "ALL"] | None = None, q: str | None = None,
            fa: bool | None = None, preset: str | None = None, cols: str | None = None, sort: str | None = None,
            f: list[str] | None = Query(None), ref: LeagueRef = Depends(open_league)):
    # The view (ffl-8iz): a saved preset (the default when none is named, or one since deleted) with
    # the URL's overrides laid over. Free agents by default; fa=0 adds every rostered player. A search
    # spans every position and both halves and ignores the filters: a lookup must not miss a man.
    q = (q or "").strip() or None
    store = _player_presets(ref=ref)
    try:
        view = player_views.resolve(store, preset, player_views.parse_params(pos=pos, fa=fa, cols=cols, f=f, sort=sort))
    except ValueError as e:
        raise HTTPException(422, {"error": str(e)}) from e
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None, ref=ref), ref=ref)
    rostered = data.get("rostered", {})  # absent from a pool cached before it existed
    # read on every request, as /roster attaches its outlooks: a finished write shows on the next load
    written = outlook.written(data["week"], ref=_outlook_ref(ref))
    capped = False
    if q:
        found = [r for half in (data["positions"], rostered) for rows in half.values() for r in rows
                 if season.name_matches(r, q)]
    else:
        # an Availability filter naming theirs or mine can only match the rostered half
        wants_rostered = any(f["field"] == "avail" and {"theirs", "mine"} & set(f["value"]) for f in view["filters"])
        halves = (data["positions"],) if view["fa"] and not wants_rostered else (data["positions"], rostered)
        spans = player_views.covers(view["pos"])
        found = [r for half in halves for p, rows in half.items() if p in spans for r in rows]
    rows = [{"owner": None, "mine": False, **r, "has_outlook": not r.get("owner") and r["player_id"] in written}
            for r in season.rank_players(found)]
    if not q:
        rows = player_views.filter_players(rows, view["filters"])
        if view["pos"] in player_views.GROUPS and not view["filters"]:
            rows, capped = player_views.cap(rows, view["sort"])
    return {"week": data["week"], "pos": None if q else view["pos"], "q": q, "fa": view["fa"], "rows": rows,
            "view": view, "capped": capped, "presets": store["presets"], "default": store["default"],
            "roster": data.get("roster", []),  # my players, for the row's comparison dropdown
            # the week ROS starts from, and whether this week's games are all played (no week term)
            "ros_week": data.get("ros_week", data["week"]), "week_over": data.get("week_over", False),
            "ros_source": data.get("ros_source", "season"), "stale_since": data["stale_since"]}


def _preset_write(change: Callable[[dict], dict], *, ref: LeagueRef | None = None) -> dict:
    """Read the presets, change them, write them back, under the lock. ValueError → 422, KeyError → 404."""
    with _PRESETS:
        try:  # read and write in one locked transaction (update_state): another process's write is not lost either
            return db.update_state("player_presets", lambda raw: change(player_views.load(raw)))
        except ValueError as e:
            raise HTTPException(422, {"error": str(e)}) from e
        except KeyError as e:
            raise HTTPException(404, {"error": f"no preset {e.args[0]!r}"}) from e


@app.get("/players/presets")
def player_presets(ref: LeagueRef = Depends(open_league)):
    return _player_presets(ref=ref)


@app.post("/players/presets")
def player_preset_create(body: dict, ref: LeagueRef = Depends(open_league)):
    pid = uuid.uuid4().hex[:8]
    return {**_preset_write(lambda s: player_views.create(s, body, pid), ref=ref), "id": pid}


@app.post("/players/presets/default")
def player_preset_default(body: dict, ref: LeagueRef = Depends(open_league)):
    return _preset_write(lambda s: player_views.set_default(s, body.get("id")), ref=ref)


@app.post("/players/presets/{pid}")
def player_preset_update(pid: str, body: dict, ref: LeagueRef = Depends(open_league)):
    return _preset_write(lambda s: player_views.update(s, pid, body), ref=ref)


@app.post("/players/presets/{pid}/delete")
def player_preset_delete(pid: str, ref: LeagueRef = Depends(open_league)):
    return _preset_write(lambda s: player_views.delete(s, pid), ref=ref)


@app.get("/league")
def league_(week: int | None = None, ref: LeagueRef = Depends(open_league)):
    data = _serve(f"league:{week or 'current'}", lambda: espn_season.league_state(week, ref=ref), ref=ref)
    return live.overlay_league(data, _live_points(data["week"], ref), _live_games(data["week"], ref), _sigma())


LOGO_TTL = 24 * 3600.0


@app.get("/logo/{team_id}")
def logo(team_id: int, ref: LeagueRef = Depends(open_league)):
    """A fantasy team's logo, fetched with the league cookies (ESPN refuses an owner's upload
    without them) and held for a day. A missing or failed logo is a 404; the UI shows its tile."""
    try:
        body, ctype = cache_for(ref).get_or_build(f"logo:{team_id}", lambda: espn_season.team_logo(team_id, ref=ref), ttl=LOGO_TTL)[0]
    except Exception as e:  # noqa: BLE001 — any failure means "no logo", never a traceback
        raise HTTPException(status_code=404, detail={"source": "espn", "error": str(e)})
    return Response(content=body, media_type=ctype, headers={"cache-control": f"private, max-age={int(LOGO_TTL)}"})


@app.get("/transactions")
def transactions(ref: LeagueRef = Depends(open_league)):
    """The league's adds, drops and trades as moves (an add beside the drop it made room with)."""
    return _serve("transactions", lambda: espn_season.transactions_state(ref=ref), ref=ref)


@app.get("/decisions")
def decisions_(week: int | None = None, kind: str | None = None, open_: bool = Query(False, alias="open"),
               ref: LeagueRef = Depends(open_league)):
    """The decision journal, newest first: the app's moves and what sessions recorded, filtered for the page."""
    rows = db.decisions(ref=ref)
    weeks = sorted({r["week"] for r in rows if r["week"] is not None}, reverse=True)
    rows = [r for r in rows if (week is None or r["week"] == week) and (kind is None or r["kind"] == kind)
            and (not open_ or r["rationale"] is None or r["outcome"] is None)]
    return {"decisions": rows, "weeks": weeks}


@app.get("/decisions/presets")
def decision_presets():
    return {k: [{"key": key, "label": label} for key, label in v] for k, v in decisions_mod.PRESETS.items()}


@app.get("/decisions/prefs")
def decision_prefs(ref: LeagueRef = Depends(open_league)):
    return _decision_prefs(ref=ref)


@app.post("/decisions/prefs")
def decision_prefs_set(body: dict, ref: LeagueRef = Depends(open_league)):
    """Settings: `log` off stops the app recording its moves (and asking why); `page` off hides the Decisions page."""
    try:
        return db.update_league_state("decision_prefs", lambda cur: decisions_mod.merge_prefs(cur or {}, body), {},
                                      ref=ref)
    except ValueError as e:
        raise HTTPException(422, {"error": str(e)}) from e


class DecisionEdit(BaseModel):
    rationale: list[str] | None = None
    note: str | None = None
    outcome: str | None = None


@app.post("/decisions/{decision_id:int}")
def decision_edit(decision_id: int, body: DecisionEdit, ref: LeagueRef = Depends(open_league)):
    """Give a decision its reason (presets, or Other with a note) and, later, its outcome. Only the fields sent change."""
    row = db.decision(decision_id, ref=ref)
    if row is None:
        raise HTTPException(404, f"no decision {decision_id}")
    sent = body.model_fields_set
    fields = {}
    if "rationale" in sent or "note" in sent:
        keys = body.rationale if "rationale" in sent else row["rationale"]
        note = body.note if "note" in sent else row["rationale_note"]
        reason = {"rationale": keys or None, "rationale_note": (note or "").strip() or None}
        # the form sends the reason back with an outcome; one already stored stands as it is (imported rows hold
        # Other with no note, which today's rule refuses), so only a changed reason is checked and written
        if reason != {k: row[k] for k in reason}:
            if err := decisions_mod.validate_rationale(row["kind"], keys, note):
                raise HTTPException(422, {"error": err})
            fields |= reason
    if "outcome" in sent:
        text = (body.outcome or "").strip() or None
        fields |= {"outcome": text, "outcome_at": decisions_mod.now_iso() if text else None}
    return db.update_decision(decision_id, fields, ref=ref) if fields else row


@app.get("/player/{player_id}/transactions")
def player_transactions(player_id: int, ref: LeagueRef = Depends(open_league)):
    """One player's moves this season (the season modal's Transactions section), off the cached
    /transactions payload: no ESPN call of its own."""
    data = _serve("transactions", lambda: espn_season.transactions_state(ref=ref), ref=ref)
    history = season.player_transactions(data["moves"], player_id)
    try:  # the draft row is the oldest; a draft that cannot be read leaves the moves standing
        picks = cache_for(ref).get_or_build("draft_picks", lambda: {"picks": espn_season.draft_picks(ref=ref)}, DRAFT_TTL)[0]["picks"]
        pick = season.draft_entry(picks, player_id, {c["team_id"]: c for c in data.get("teams", [])})
    except Exception as e:  # noqa: BLE001 — HTTPException from the cache included
        print(f"draft pick unavailable for {player_id}: {e}")
        pick = None
    try:  # a section, like the draft row: a journal that cannot be read leaves the moves standing
        mine = [d for d in db.decisions(ref=ref) if player_id in (d["player_ids"] or [])]
    except db.DBError as e:
        log.warning("decisions unavailable for %s: %s", player_id, e)
        mine = []
    return {"history": history + ([pick] if pick else []), "decisions": mine, "stale_since": data["stale_since"]}


DRAFT_TTL = 24 * 3600.0  # the draft is over; the record does not move


@app.get("/roster")
def roster_(ref: LeagueRef = Depends(open_league)):
    data = _serve("roster", lambda: espn_season.roster_state(None, ref=ref), ref=ref)
    # gated on the payload's week, as /week is: after the rollover the feed still holds last week's
    wk = data["week"]
    data = live.overlay_roster(data, _live_points(wk, ref), _live_games(wk, ref), _live_stats(wk, ref), _scoring(ref=ref))
    # attach on every request, not inside the cached build: a finished generation run must show
    # on the next load, not after the TTL
    o = _outlook_ref(ref)
    return {**data, "cards": outlook.attach(data["cards"], outlook.load(wk, ref=o), outlook.writing(ref=o)),
            "outlook_run": {**outlook.last(o), "running": outlook.running()}}


def _outlook_ref(ref: LeagueRef | None) -> LeagueRef | None:
    """The league as the outlook store takes it: home is None, as its calls were before every league had one."""
    return None if ref is None or ref.home else ref


def _roster_card(player_id: int, *, ref: LeagueRef | None = None) -> tuple[dict, int]:
    """His card in the cached roster and its week; 404 once he is off the roster."""
    data = _serve("roster", lambda: espn_season.roster_state(None, ref=ref), ref=ref)
    for c in data["cards"]:
        if c["player_id"] == player_id:
            return c, data["week"]
    raise HTTPException(status_code=404, detail=f"player {player_id} is not on the roster")


@app.get("/outlook/{player_id:int}")
def card_outlook(player_id: int, ref: LeagueRef = Depends(open_league)):
    card, week = _roster_card(player_id, ref=ref)
    return outlook.roster_entry(card, week, ref=_outlook_ref(ref))


@app.post("/outlook/{player_id:int}", status_code=202)
def card_outlook_write(player_id: int, ref: LeagueRef = Depends(open_league)):
    """One Roster card's outlook, rewritten in the background whether or not his facts moved; the card
    polls the GET until `outlook_running` clears. 409 while the full run holds the Roster file."""
    card, week = _roster_card(player_id, ref=ref)
    try:
        outlook.start_one(card, week, run=outlook.run_claude, kind=outlook.ROSTER, ref=_outlook_ref(ref))
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"started": True}


def _roster_job(ref: LeagueRef) -> Callable[[], tuple[list[dict], int]]:
    """A league's (cards, week) for the outlook run, read when the run reaches it."""
    def build():
        state = espn_season.roster_state(None, ref=ref)
        return state["cards"], state["week"]
    return build


@app.post("/outlook/refresh", status_code=202)
def outlook_refresh(league: str | None = Query(None)):
    """The hourly run (launchd/com.jonulak.ffl-outlook): every league in turn, one run, home's cards read first so
    a failure to read them is this answer's 502. With ?league= only that league, an empty one home's (the Roster
    page's button, which must not start every league's run)."""
    first = open_league(league)
    rest = [] if league is not None else [r for r in leagues.all_leagues() if r.key != first.key]
    try:
        cards, week = _roster_job(first)()
    except Exception as e:  # noqa: BLE001 — a source failure is a 502, not a bare traceback
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    jobs = [(_outlook_ref(first), lambda: (cards, week))] + [(_outlook_ref(r), _roster_job(r)) for r in rest]
    try:
        outlook.start_leagues(jobs, run=outlook.run_claude)
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    # pending needs the news context, which is fetched in the run's thread; the page reads the count from outlook_run
    return {"started": True, "pending": None}


def _free_agent(player_id: int, *, ref: LeagueRef | None = None) -> tuple[dict, int]:
    """His row in the cached pool and its week; 404 once he is off the wire."""
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None, ref=ref), ref=ref)
    for rows in data["positions"].values():
        for r in rows:
            if r["player_id"] == player_id:
                return r, data["week"]
    raise HTTPException(status_code=404, detail=f"player {player_id} is not a free agent")


@app.get("/waivers/outlook/{player_id}")
def waiver_outlook(player_id: int, ref: LeagueRef = Depends(open_league)):
    row, week = _free_agent(player_id, ref=ref)
    return outlook.waiver_entry(row, week, ref=_outlook_ref(ref))


@app.post("/waivers/outlook/{player_id}", status_code=202)
def waiver_outlook_write(player_id: int, ref: LeagueRef = Depends(open_league)):
    """One free agent's outlook, written in the background (a minute or two of Opus with web search);
    the dialog polls the GET until `running` clears."""
    row, week = _free_agent(player_id, ref=ref)
    try:
        outlook.start_one(row, week, run=outlook.run_claude, ref=_outlook_ref(ref))
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"started": True}


def sync_all(*, ref: LeagueRef | None = None) -> dict:
    # espn_api caches current_week and the records in League.__init__; a months-old League would
    # make sync_results upsert stale scores for the weeks it still knows about.
    r = ref if ref is not None else leagues.home()
    espn_season.refresh_league()
    lg = espn_season.fresh_league(r)
    settings = load(r)
    sync_transactions(lg, ref=r)
    sync_results(lg, settings, ref=r)
    return {"transactions": True, "results": True}


def _sync_league(ref: LeagueRef) -> int:
    lg = espn_season.fresh_league(ref)
    new = sync_transactions(lg, ref=ref)
    sync_results(lg, load(ref), ref=ref)
    return len(new)  # new rows already dropped the Transactions page (journal.ON_NEW_ROWS)


@app.post("/transactions/sync")
def transactions_sync(league: str | None = Query(None)):
    """The hourly tick (launchd/com.jonulak.ffl-transactions): ESPN's activity feed into
    the DB's transactions table, so the record keeps up with no page open, and the weekly results into
    `results` (the nav's Refresh did that until it went). Concurrent syncs are safe: the table's
    (season, id) key dedupes. With ?league= that league alone; with none every league, home's answer at the top
    (its failure the 502, as before) and each other's under `leagues` (a failure reported there)."""
    first, others, failed = open_league(league), {}, None
    try:
        n = _sync_league(first)
    except Exception as e:  # noqa: BLE001 — curl's non-zero exit on the 502 is the log line, once the others ran
        failed = e
    for r in [] if league else leagues.all_leagues():
        if r.key == first.key:
            continue
        try:
            others[r.key] = {"new": _sync_league(r)}
        except Exception as e:  # noqa: BLE001 — another league's outage is its own line, never home's 502
            others[r.key] = {"error": str(e)}
    more = {"leagues": others} if others else {}
    if failed is not None:
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(failed), **more})
    return {"new": n, **more}


# --- push notifications (ffl/notify.py): the 15-minute tick, the device subscriptions, the Settings page ---

_NOTIFY = threading.Lock()  # one tick at a time: two would read the same unsent notices and push them twice


class SubscriptionIn(BaseModel):
    endpoint: str
    keys: dict[str, str]
    ua: str = ""


class EndpointIn(BaseModel):
    endpoint: str


@app.get("/notify/config")
def notify_config(ref: LeagueRef = Depends(open_league)):
    """The devices and quiet hours (every league's) and the open league's switches."""
    return {"public_key": os.environ.get("VAPID_PUBLIC_KEY"), "enabled": notify.sender() is not None,
            "prefs": notify.prefs(ref=ref), "devices": len(db.subscriptions())}


@app.post("/notify/subscribe")
def notify_subscribe(body: SubscriptionIn):
    db.put_subscription(body.endpoint, body.keys, body.ua)
    return {"ok": True}


@app.post("/notify/unsubscribe")
def notify_unsubscribe(body: EndpointIn):
    db.drop_subscription(body.endpoint)
    return {"ok": True}


@app.post("/notify/prefs")
def notify_prefs(body: dict, ref: LeagueRef = Depends(open_league)):
    try:
        return notify.put_prefs(body, ref=ref)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/notify/test")
def notify_test():
    out = notify.send_test()
    if out.get("disabled"):
        raise HTTPException(status_code=503, detail="notifications are off on this API (FFL_NOTIFY=0 or no VAPID key)")
    return out


class TraceIn(BaseModel):
    event: str
    fields: dict = {}


@app.post("/trace")
def trace(body: TraceIn):
    """On-device trace of the push tap path (ffl-e9x.10): the worker and the page beacon each step here, and the
    server stamps the time, since uvicorn's access log carries none. One row per event in the `traces` table
    (`db.trace_rows()` reads them back as the old trace.jsonl lines)."""
    try:
        db.add_trace(datetime.now().astimezone().isoformat(timespec="milliseconds"), {"event": body.event, **body.fields})
    except db.DBError as e:  # a debug beacon: nothing retries it, so say so in the log and answer
        log.warning("trace %s not saved: %s", body.event, e)
        return {"ok": False}
    return {"ok": True}


@app.get("/notify/recent")
def notify_recent():
    return {"notices": db.recent_notices()}


@app.post("/notify/tick")
def notify_tick():
    """launchd/com.jonulak.ffl-notify every 15 minutes: sync, detect, flush (notify.tick), then the pre-game pull."""
    if not _NOTIFY.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a notify tick is already running")
    try:
        out = notify.tick()
        # every league's own steps (home's in the answer's top level, as before; the others under `leagues`), the
        # NFL's plays once for all of them
        _tick_steps(out, (("corrections", correction_pass),))
        _tick_steps(out, (("final_plays", final_plays),))
        _tick_steps(out, (("watch", watch_pass), ("pregame", pregame_pull)))
        for r in leagues.all_leagues():
            if not r.home:
                _tick_steps(out.setdefault("leagues", {}).setdefault(r.key, {}),
                            (("corrections", correction_pass), ("watch", watch_pass), ("pregame", pregame_pull)), ref=r)
    finally:
        _NOTIFY.release()
    return out


def _tick_steps(out: dict, steps, *, ref: LeagueRef | None = None) -> None:
    """Each (name, step) into `out`, for home with no argument or for the league `ref`; one that raises is reported
    in the answer like a tick step and never fails the tick."""
    for name, fn in steps:
        try:
            out[name] = fn() if ref is None else fn(ref=ref)
        except Exception as e:  # noqa: BLE001 — reported in the answer like a tick step, never fails the tick
            out[name] = {"error": str(e)}


# --- the news desk (ffl/newsdesk/pipeline.py): launchd/com.jonulak.ffl-news every 10 minutes ---

_NEWS = threading.Lock()  # one run at a time: two would poll, tag and write the same items twice
_NEWS_THREAD: list[threading.Thread] = []  # the last run's thread (tests join it)


def _news_run() -> None:
    try:
        pipeline.run()
    finally:
        _NEWS.release()
    # its pushes go now rather than at the next tick; a tick holding the lock flushes them itself
    if _NOTIFY.acquire(blocking=False):
        try:
            notify.flush()
        except db.DBError as e:
            log.warning("news: flush failed: %s", e)
        finally:
            _NOTIFY.release()


@app.post("/news/run", status_code=202)
def news_run():
    """Collect, tag and write the news, queue its pushes and flush them, on a thread: a run takes minutes."""
    if not _NEWS.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a news run is already running")
    t = threading.Thread(target=_news_run, daemon=True, name="news-run")
    _NEWS_THREAD[:] = [t]
    t.start()
    return {"started": True}


def _news_context(ref: LeagueRef | None = None) -> dict:
    """What the feed is dressed with: the open league's owners (read now, never stored; a failure serves the feed
    untagged with `owners_error`), the player index (names; a failure leaves them as ids with `index_error`) and the
    sources. The stories are every league's; only whose they are is the league's."""
    ctx: dict = {"owners": {}, "me": -1, "extra": {}, "index": news_players.Index()}
    try:
        ctx["owners"], ctx["me"] = pipeline.owners() if _key(ref) is None else pipeline.owners(ref=ref)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — no league config exits; the feed still reads
        ctx["extra"]["owners_error"] = str(e) or type(e).__name__
    try:
        ctx["index"] = news_players.fetch_index()
    except (Exception, SystemExit) as e:  # noqa: BLE001
        ctx["extra"]["index_error"] = str(e) or type(e).__name__
    ctx["sources"] = {s["id"]: s for s in db.news_sources(active_only=False)}
    return ctx


def _news_page(ctx: dict, limit: int, before: str | None, *, filter: str = "all", player: int | None = None,
               since: str | None = None) -> dict:
    """Read pages until `limit` stories pass the filter or the table ends (next_before None). Only the stories that
    move somebody (a ▲/▼ row) count, so a page fills with them rather than thinning."""
    kept: list[dict] = []
    while True:
        raw = db.news_feed(before, limit, player_id=player, since=since)
        for s in news_feed.shape(raw, ctx["owners"], ctx["me"], ctx["index"], ctx["sources"], player):
            if news_feed.moves(s) and news_feed.keep(s, filter):
                kept.append(s)
                if len(kept) == limit:
                    return {"stories": kept, "next_before": f"{s['at']}|{s['id']}"}
        if len(raw) < limit:
            return {"stories": kept, "next_before": None}
        before = f"{raw[-1]['latest_at']}|{raw[-1]['id']}"


@app.get("/news")
def news_get(filter: Literal["all", "mine", "fa", "others"] = "all", before: str | None = None,
             limit: int = Query(30, ge=1, le=100), ref: LeagueRef = Depends(open_league)):
    """The feed: the stories that move somebody; one in which every player is ● (no change) never shows."""
    ctx = _news_context(ref)
    return {**_news_page(ctx, limit, before, filter=filter), **ctx["extra"]}


@app.get("/news/story/{story_id}")
def news_story(story_id: int, ref: LeagueRef = Depends(open_league)):
    """One written story, shaped as the feed shapes it: a push's target, which the feed may hide (it moves nobody) or have paged past."""
    ctx = _news_context(ref)
    raw = db.story(story_id)
    if raw is None or not raw.get("written_rev"):
        raise HTTPException(404, f"no story {story_id}")
    return {**news_feed.shape([raw], ctx["owners"], ctx["me"], ctx["index"], ctx["sources"])[0], **ctx["extra"]}


@app.get("/news/player/{player_id}")
def news_player(player_id: int, days: int = Query(30, ge=1, le=366), before: str | None = None,
                limit: int = Query(30, ge=1, le=100), ref: LeagueRef = Depends(open_league)):
    ctx = _news_context(ref)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    muted = player_id in {int(p) for p in db.get_state("news_mutes", []) or []}
    return {"player": news_feed.player_head(player_id, ctx["owners"], ctx["me"], ctx["index"]), "muted": muted,
            **_news_page(ctx, limit, before, player=player_id, since=since), **ctx["extra"]}


@app.get("/news/search")
def news_search(q: str = ""):
    if not q.strip():
        return {"players": []}
    try:
        index = news_players.fetch_index()
    except (Exception, SystemExit) as e:  # noqa: BLE001
        raise HTTPException(502, {"source": "espn", "error": f"player index: {str(e) or type(e).__name__}"}) from e
    return {"players": news_feed.search(index, q)}


class MuteIn(BaseModel):
    muted: bool


@app.post("/news/mute/{player_id}")
def news_mute(player_id: int, body: MuteIn):
    def change(cur):
        muted = {int(p) for p in cur or []}
        return sorted(muted | {player_id} if body.muted else muted - {player_id})
    db.update_state("news_mutes", change, [])
    return {"muted": body.muted}


@app.get("/news/status")
def news_status():
    cols = ("id", "name", "kind", "active", "ok_at", "error", "failing_since")
    return {"sources": [{k: s[k] for k in cols} for s in db.news_sources(active_only=False)],
            "last_run": db.get_state("news_last_run"),
            # {UTC date: {input, output, calls}}, the last 30 days (ffl/newsdesk/usage.py)
            "haiku_usage": db.get_state(news_usage.HAIKU, {}) or {}, "jev_usage": db.get_state(news_usage.JEV, {}) or {}}


def correction_pass(*, ref: LeagueRef | None = None) -> dict:
    """The tick's stat-correction step for a league (home when `ref` is None; espn_season.correct_boxes): a
    starter's corrected points go to his week's play feed (the runner's store when that is the week it holds, so
    the board sees it without a restart), and every page built on a changed week rebuilds behind its next request."""
    out = espn_season.correct_boxes(espn_season.fresh_league(ref), ref=ref)
    plays = out.pop("plays")
    runner = _lane(ref)
    cache_ = cache_for(ref)
    for w in sorted({p["week"] for p in plays}):
        rows = [p for p in plays if p["week"] == w]
        try:
            if runner and runner.store is not None and runner.week == w:
                runner.store.add(rows)
            else:
                db.add_plays(rows, ref=ref)
        except db.DBError as e:
            log.warning("stat correction plays for week %s not saved: %s", w, e)
            out.setdefault("errors", []).append(f"plays week {w}: {e}")
    for w in out["changed"]:
        for key in (f"week:{w}", f"league:{w}"):
            cache_.expire(key)
    if out["changed"]:
        for key in ("roster", "waivers", "player", "team"):  # form rows and add read earlier weeks' points
            cache_.expire(key)
    return out


def expire_everything(*, ref: LeagueRef | None = None) -> None:
    """Every page of the league (home when `ref` is None) and every projection/injury cache refetches on its next
    read. Expire, not clear: a pull while ESPN is down must still leave the stale payloads behind for the fallback
    to serve, flagged. The NFL-wide caches are expired whichever league asks."""
    cache_for(ref).expire_all()
    espn_season._SL.clear()
    espn_season._SEASON_CACHE.clear()
    espn_season.forget_current_boxes(ref=ref)  # finished weeks stay: they change only through the correction pass
    if espn_season._INJ:  # expire, not clear: _INJ is the last-good store the outage fallback serves
        espn_season._INJ[0] = 0.0
    sources.expire_all()  # same reason: the last good scrape stays for an outage
    nfl.expire_all()  # the NFL tab's scoreboard and feeds


def pregame_pull(now: datetime | None = None, *, ref: LeagueRef | None = None) -> list[str]:
    """Once per kickoff time, inside season.PREGAME_PULL_MIN of it (the inactives are out): expire everything of the
    league (home when `ref` is None) and rebuild its pages on a thread behind the tick. Answers the kickoffs pulled
    for."""
    kicks = [k for ks in espn_season.kickoffs_by_week(espn_season.fresh_league(ref)).values() for k in ks]
    pulled = set(db.get_league_state("pregame_pulls", [], ref=ref) or [])
    due = season.pregame_due(kicks, now or datetime.now(timezone.utc), pulled)
    if not due:
        return []
    expire_everything(ref=ref)
    c = cache_for(ref)
    t = threading.Thread(target=c.rebuild_expired, daemon=True, name="cache-pregame")
    with c._lock:
        c._threads["pregame"] = t
    t.start()
    new = {k.isoformat() for k in due}
    db.update_league_state("pregame_pulls", lambda cur: sorted(set(cur or []) | new)[-40:], [], ref=ref)  # a season's kickoffs
    return [k.isoformat() for k in due]


WATCH_EVERY = timedelta(hours=1)


def watch_pass(*, ref: LeagueRef | None = None) -> dict:
    """The tick's watchlist step, at most once an hour: every detector over every watched player (whoever holds
    him), the new signals stored, and one push a day for a free agent with something new (`available` included:
    it fires only once he is free). A player on another roster is never pushed. `watch_state` holds what the
    detectors compare against: who has stood ahead of him, his last worth to my roster, his last owner; it is kept
    only for the watched ids. Prices and signals come off the cached pool (up to an hour old); the pushes are gated
    on a fresh owners read, and a failed one queues nothing. Nobody watched: no pool build at all. One league's
    pass (home when `ref` is None): its watchlist, pool, owners and pushes."""
    st = db.get_league_state("watch_state", {}, ref=ref) or {}
    at = notify.now()
    t = datetime.fromisoformat(at)
    if st.get("last_pass") and t - datetime.fromisoformat(st["last_pass"]) < WATCH_EVERY:
        return {"skipped": "hourly"}
    if not db.watched_ids(ref=ref):
        return {"skipped": "none watched"}
    day = at[:10]
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None, ref=ref), ref=ref)
    chart = news.fetch_depth()
    # ESPN's feed by club and position (norm name -> status, and -> its name for the chip), laid over Sleeper's
    feed: dict[tuple, dict[str, str | None]] = {}
    names: dict[tuple, dict[str, str]] = {}
    for row in espn_season.fetch_injuries().values():
        if row.get("team") and row.get("pos") and row.get("name"):
            n = news.norm_name(row["name"])
            feed.setdefault((row["team"], row["pos"]), {})[n] = row.get("status")
            names.setdefault((row["team"], row["pos"]), {})[n] = row["name"]
    seen, bench, owner = st.get("above", {}) or {}, st.get("bench", {}) or {}, st.get("owner", {}) or {}
    found, rows = [], {}
    for p in _watched(data, ref=ref):
        pid, k, club = p["player_id"], str(p["player_id"]), (p.get("team"), p["pos"])
        rows[pid] = p
        ahead, above = watch.ahead_signals(p, chart.get(p.get("team"), []), feed.get(club, {}),
                                           set(seen.get(k, [])), at, names.get(club, {}))
        found += ahead
        weakest = watch.weakest_at(data.get("roster", []), p["pos"])
        priced = p.get("delta_roster") is not None  # a row priced without a worth to me has nothing to cross
        found += [s for s in (watch.usage_signal(p, at),
                              priced and watch.bench_signal(p, bench.get(k), weakest, data.get("ros_week", data["week"]), at),
                              watch.available_signal(p, owner.get(k), day, at)) if s]
        seen[k], bench[k] = sorted(above), p.get("delta_roster")
        owner[k] = (p.get("owner") or {}).get("team_id")
    new = db.add_watch_signals(found, ref=ref)
    by_player: dict[int, list[dict]] = {}
    for s in new:
        by_player.setdefault(s["player_id"], []).append(s)
    out: dict = {"signals": len(new)}
    notices = []
    if by_player:
        try:
            fresh, me = pipeline.owners(ref=ref)  # the pool's owners can be an hour old: one just added must not be pushed
        except (Exception, SystemExit) as e:  # noqa: BLE001 — never fall back to the stale owner; the signals stand
            log.warning("watch pass: owners read failed, nothing queued: %s", e)
            out["owners"] = str(e)
            fresh = None
        for pid, sigs in by_player.items() if fresh is not None else ():
            holder = (fresh.get(pid) or {}).get("team_id")
            if holder is not None and holder == me:
                db.put_watch(pid, False, ref=ref)  # mine now, like `_watched`
            elif pid not in fresh and rows[pid].get("owner") is None:
                notices.append(watch.notice_for(rows[pid], sigs, day, at))
    out["queued"] = db.add_notices(notices, ref=ref)
    _prune_watch_state(set(db.watched_ids(ref=ref)), {"last_pass": at, "above": seen, "bench": bench, "owner": owner},
                       ref=ref)
    return out


@app.post("/refresh")
def refresh(ref: LeagueRef = Depends(open_league)):
    """A forced pull by hand (curl; the nav button is gone, the notify tick's pregame_pull runs one before games).
    Waits for every page to rebuild."""
    expire_everything(ref=ref)
    try:
        synced = sync_all(ref=ref)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — an ESPN failure is a 502, not a bare traceback
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    cache_for(ref).rebuild_expired()
    return {"synced": synced, "at": datetime.now().strftime("%H:%M")}
