"""FastAPI glue for the season UI: ESPN/Sleeper state behind a short cache, a few writes.

    uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300

Loopback only; Next.js on :3300 proxies /api/* here. Stale payloads are kept past the TTL and
served, flagged, when a source is down — never a blank page on Sunday.

Only these endpoints write anything: POST /refresh runs the journal sync, POST /outlook/refresh starts a background run that writes
the week's outlooks to the DB, POST /waivers/outlook/{id} writes one free agent's the same way (kind waivers-week),
POST /claims saves the week's claim plan to the DB (never touches ESPN), and POST /claims/submit is
the one route that writes to ESPN (instant free-agent adds + waiver claims, through ffl/espn_write.py) and journals it;
POST /lineup writes to ESPN too (moves players between lineup slots, one transaction) and journals it.
Every GET is read-only.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Literal

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import calibrate, cbs, db, espn_season, espn_write, live, outlook, player_card, season
from . import claims as claims_mod
from . import db
from . import plays as plays_mod
from .config import JOURNAL
from .journal import sync_results, sync_transactions
from .settings import load

log = logging.getLogger(__name__)
TTL = 120.0


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
        started = time.time()
        try:
            payload = hit.fn()
        except Exception as e:  # noqa: BLE001 — any source failure keeps the stale payload
            print(f"cache rebuild of {key} failed: {e}")
            hit.failed = True
            return
        now = time.time()
        with self._lock:
            cur = self._d.get(key)
            if cur is not None and cur.built_at > started:
                return  # something was installed (put) while this build ran; its read is older
            self._d[key] = Entry(now + hit.ttl, now, payload, hit.fn, hit.ttl)

    def put(self, key: str, payload: object) -> None:
        """Install a payload built by the caller, fresh. A revalidate that began before this lands cannot replace it."""
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                return
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

    def expire(self, prefix: str):
        """Mark `prefix` and every `prefix:...` key expired: served stale once, rebuilt behind the request."""
        with self._lock:
            for k, e in self._d.items():
                if k == prefix or k.startswith(prefix + ":"):
                    e.expires = 0.0

    def drop(self, key: str):
        """Forget `key`: the next request builds it inline, never served from the old payload."""
        with self._lock:
            self._d.pop(key, None)

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
    # Warm the page caches so the first browser hit after a (re)start is not the cold build.
    # Registered with the cache's thread table so join() covers it; a failure is logged and the
    # request path builds inline as it always did.
    t = threading.Thread(target=warm, daemon=True, name="cache-warm")
    cache._threads["warm"] = t
    t.start()
    # One FastCast subscription per process; overlays live points on the cached payloads and
    # wakes /live. FFL_LIVE=0 keeps it off (tests, a second dev API).
    runner = live.Runner(week_fn=lambda: cache.get_or_build("starters:current", lambda: espn_season.league_starters(None))[0]) \
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


def _serve(key: str, fn: Callable[[], dict], ttl: float | None = None) -> dict:
    payload, stale = cache.get_or_build(key, fn, ttl)
    return {**payload, "stale_since": stale}


@app.get("/health")
def health():
    return {"ok": True}


# --- waiver claim groups ("get K of N"): plan file, instant adds, ESPN claims with read-back ---

DECISIONS = JOURNAL / "DECISIONS.md"
_SUBMIT = threading.Lock()  # one submit at a time: it cancels and reposts ESPN's whole claim list


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


def _instant_adds() -> bool:
    # Read at call time: off until ffl-ut0.7 verifies the FREEAGENT write shape (anything but "1" is off).
    return os.environ.get("FFL_INSTANT_ADDS") == "1"


def _roster_size() -> int:
    return int(load()["roster_size"])


def _load_plan(week):
    # a plan the waiver run already processed reads as retired (an empty week); the next save writes that down
    plan = db.claim_plan(week)
    if plan is None:
        return {"week": week, "groups": [], "submits": []}
    return claims_mod.retire(plan, espn_write.last_waiver_run())


def _save_plan(plan):
    plan["saved_at"] = datetime.now().isoformat(timespec="seconds")
    db.put_claim_plan(plan)


def _pool_rows(data):
    return {r["player_id"]: r for rows in data["positions"].values() for r in rows}


def _status(data):
    return {i: r.get("status") for i, r in _pool_rows(data).items() if r.get("status")}


def _fresh_status(data):
    """ESPN's statuses now (the pool is cached, and the Wednesday run turns WAIVERS into FREEAGENT); the cached
    ones when the read fails, where unknown counts as WAIVERS."""
    try:
        return {i: v["status"] for i, v in espn_season.pool_status().items() if v.get("status")}
    except Exception:  # noqa: BLE001
        return _status(data)


def _players(groups, data):
    rows, roster = _pool_rows(data), {r["player_id"]: r for r in data.get("roster", [])}
    want = {i for g in groups for i in g["targets"] + g["cuts"]} | set(roster)
    keys = ("name", "pos", "team", "add", "ros_adj", "slot", "status", "waiver_date", "droppable", "locked")
    return {str(i): {k: (rows.get(i) or roster.get(i)).get(k) for k in keys} for i in want if rows.get(i) or roster.get(i)}


def _claims_payload(plan, data, status=None):
    roster, status = data.get("roster", []), _status(data) if status is None else status
    last = plan["submits"][-1] if plan["submits"] else None
    ids = set(last["ids"]) if last else set()
    now, expansion = claims_mod.plan_now(plan["groups"], status), claims_mod.expand(plan["groups"], status)
    try:
        pending, perr = espn_write.pending_claims(), None
    except espn_write.WriteError as e:
        pending, perr = [], ("ESPN login expired, refresh .env" if e.expired else str(e))
    key = lambda ms: [(m["add"], m["drop"]) for m in ms]  # noqa: E731
    # ESPN holds the plan's claims, in order, once the claims filed on ESPN directly (kept outside) are set aside
    # and only when ESPN numbered them: an unnumbered claim (priority None) means its order is unknown
    on_espn = (bool(last) and perr is None and all(c.get("priority", 0) is not None for c in pending)
               and claims_mod.matches(expansion, [c for c in pending if c["id"] in ids]))
    return {"week": plan["week"], "groups": plan["groups"], "now": now, "claims": expansion,
            "errors": claims_mod.validate(plan["groups"], roster, set(_pool_rows(data)), _roster_size(), status,
                                          _instant_adds()),
            "open_spots": claims_mod.open_spots(roster, _roster_size()),
            "status": {str(i): s for i, s in status.items()},
            "pending": [{**c, "from_app": c["id"] in ids} for c in pending], "pending_error": perr,
            "last_submit": last,
            # claims only: instant adds already happened, and the plan's free agents are on the roster after a refresh
            "edited_since_submit": bool(last) and key(last["claims"]) != key(expansion),
            "on_espn": on_espn,
            "players": _players(plan["groups"], data)}


@app.get("/claims")
def claims_get(fresh: bool = False):
    # fresh=1 (the Submit dialog on open): statuses re-read from ESPN, so "Add now" is what Submit will run
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    return _claims_payload(_load_plan(data["week"]), data, _fresh_status(data) if fresh else None)


@app.post("/claims")
def claims_save(body: PlanIn):
    # a save during a submit would overwrite its record (or be overwritten by it)
    if not _SUBMIT.acquire(blocking=False):
        raise HTTPException(409, "a submit is running")
    try:
        data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
        plan = {**_load_plan(data["week"]), "groups": [g.model_dump() for g in body.groups]}
        _save_plan(plan)
        return _claims_payload(plan, data)
    finally:
        _SUBMIT.release()


@app.post("/claims/submit")
def claims_submit(body: SubmitIn):
    if not _SUBMIT.acquire(blocking=False):
        raise HTTPException(409, "a submit is already running")
    try:
        data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
        plan, roster = _load_plan(data["week"]), data.get("roster", [])
        status = _fresh_status(data)
        # the dialog showed an "Add now" list from its own read: instant adds can't be undone, so any other is a 409
        now = claims_mod.plan_now(plan["groups"], status)
        if now != [m.model_dump() for m in body.expect_now]:
            raise HTTPException(409, {"error": "statuses changed — review again", "now": now})
        errs = claims_mod.validate(plan["groups"], roster, set(_pool_rows(data)), _roster_size(), status, _instant_adds())
        if errs:
            raise HTTPException(422, {"errors": errs})
        try:
            pending, period = espn_write.pending_state()
        except espn_write.WriteError as e:
            raise HTTPException(502, {"source": "espn", "error": "ESPN login expired, refresh .env" if e.expired else str(e)})
        # ESPN's current scoring period, which is what its write bodies want; the plan's week is the cached pool's
        if period is not None and period != data["week"]:
            raise HTTPException(409, {"error": "ESPN's week changed — reload"})
        last = plan["submits"][-1] if plan["submits"] else None
        wk = data["week"] if period is None else period
        w = SimpleNamespace(pending_claims=espn_write.pending_claims,
                            cancel_claim=lambda i: espn_write.cancel_claim(i, wk),
                            submit_claim=lambda a, d: espn_write.submit_claim(a, d, wk),
                            add_free_agent=lambda a, d: espn_write.add_free_agent(a, d, wk),
                            reorder_claims=espn_write.reorder_claims)
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
        _save_plan(plan)
        if res["now"] or res["sent"]:
            names = {i: r["name"] for i, r in _pool_rows(data).items()} | {r["player_id"]: r["name"] for r in roster}
            with DECISIONS.open("a") as f:
                f.write("\n" + claims_mod.decision_entry(submitted, names, datetime.now().strftime("%Y-%m-%d"), status))
        if res["now"]:
            # the roster changed: as POST /refresh does, rebuild inline so the next page shows the add, not the dropped player
            espn_season._BOXES.clear()
            cache.expire_all()
            cache.rebuild_expired()
        return res
    finally:
        _SUBMIT.release()


class LineupMove(BaseModel):
    player_id: int
    to_slot: str


class LineupIn(BaseModel):
    moves: list[LineupMove]


_LINEUP = threading.Lock()  # one move at a time: the answer is read back from ESPN, and two would read each other's half


def _espn_error(e: espn_write.WriteError) -> HTTPException:
    return HTTPException(502, {"source": "espn", "error": "ESPN login expired, refresh .env" if e.expired else str(e)})


@app.post("/lineup")
def lineup(body: LineupIn):
    """Move players between ESPN slots (one transaction), then answer with the Roster page as ESPN now has it.
    Locks are re-read here, not trusted from the page: a game can kick off between the load and the tap."""
    if not _LINEUP.acquire(blocking=False):
        raise HTTPException(409, "a lineup move is already running")
    try:
        data = _serve("roster", lambda: espn_season.roster_state(None))
        # the two reads are independent: both in flight at once (0.45 s + 0.2 s, one after the other)
        with ThreadPoolExecutor(2) as pool:
            reading = pool.submit(lambda: espn_season.roster_flags(espn_season.fresh_league()))
            pending = pool.submit(espn_write.pending_state)
        try:
            flags = reading.result()
            _, period = pending.result()
        except espn_write.WriteError as e:
            raise _espn_error(e)
        except Exception as e:  # noqa: BLE001 — the mRoster read
            raise HTTPException(502, {"source": "espn", "error": str(e)})
        if period is not None and period != data["week"]:
            # the League is cached up to LEAGUE_TTL_S: drop it and the page, or the Reload rebuilds the same old week
            espn_season.refresh_league()
            cache.drop("roster")
            raise HTTPException(409, {"error": "ESPN's week changed — reload"})
        # slots and locks both from this one mRoster read: the cached page may be minutes behind
        # under FFL_WRITES=0 the page shows the fake store's slots, so the moves are validated against those ({} for real)
        roster = espn_season.with_espn_slots(data["cards"], flags, espn_write.fake_slots())
        items, errs = season.lineup_moves([m.model_dump() for m in body.moves], roster, espn_season.load())
        if errs:
            raise HTTPException(409, {"errors": errs})
        if items:
            uncertain = False
            try:
                espn_write.move_lineup(items, data["week"])
            except espn_write.WriteError as e:
                raise _espn_error(e)
            except requests.RequestException:  # ESPN may have executed it before the connection died
                uncertain = True
            # journaled the moment ESPN may have taken the move, whatever the read-back does; a fake move
            # (FFL_WRITES=0) is not a decision, so it only goes to the log
            try:
                entry = season.lineup_entry(items, data["cards"], data["week"], datetime.now().strftime("%Y-%m-%d"))
                if uncertain:
                    entry += "\n- ESPN did not answer; the move may not have happened — check ESPN."
                if espn_write.writes():
                    with DECISIONS.open("a") as f:
                        f.write("\n" + entry)
                else:
                    log.info("fake lineup move (FFL_WRITES=0), not journaled: %s", entry)
            except OSError as e:
                log.warning("lineup move made but not journaled: %s", e)
            # the moved players keep the rows they took (season.reseat), before the read-back reads the seats
            try:
                db.put_seats(season.reseat(items, data["cards"], db.seat_map()))
            except db.DBError as e:
                log.warning("lineup moved but its rows not kept: %s", e)
            espn_season._BOXES.pop(data["week"], None)  # the box scores carry the slots; read them again
            cache.expire("week")  # the Matchup board shows this lineup too
            if uncertain:
                cache.drop("roster")
                raise HTTPException(502, {"source": "espn", "error": "ESPN did not answer — it may have moved; reload", "moved": True})
            # the read-back reuses this request's mRoster read, the moves applied (ESPN said EXECUTED): a second
            # read would only say the same; locks cannot have changed in the second since
            moved = {**flags, **{p: {**flags.get(p, {}), "slot": to} for p, _, to in items}}
            try:
                cache.put("roster", espn_season.roster_state(None, flags=moved))
            except Exception as e:  # noqa: BLE001 — ESPN took the move; say so, never show the old slots as current
                cache.drop("roster")  # the reload this asks for must build from ESPN, not serve the pre-move page
                raise HTTPException(502, {"source": "espn", "error": "moved in ESPN — the read-back failed, reload", "moved": True}) from e
        return roster_()
    finally:
        _LINEUP.release()


class SitIn(BaseModel):
    sat: bool


@app.post("/roster/sit/{player_id:int}")
def roster_sit(player_id: int, body: SitIn):
    """Sit him for the week, or stand him back up: the call is re-made without him (season.roster_cards) and the
    page answers at once. Nothing reaches ESPN. Under the lineup lock, so a move's read-back and a sit never land
    over each other."""
    if not _LINEUP.acquire(blocking=False):
        raise HTTPException(409, "a lineup move is running")
    try:
        data = _serve("roster", lambda: espn_season.roster_state(None))
        before = next((c for c in data["cards"] if c["player_id"] == player_id), None)
        if before is None:
            raise HTTPException(404, f"player {player_id} is not on the roster")
        week = data["week"]
        if before.get("sat") != body.sat:
            try:
                db.put_sit(week, player_id, body.sat)
            except db.DBError as e:
                raise HTTPException(500, f"sit not saved: {e}") from e
            # a dev API (FFL_WRITES=0) is not the manager deciding, as with a fake lineup move: log only
            entry = season.sit_entry(before, body.sat, week, datetime.now().strftime("%Y-%m-%d"))
            try:
                if espn_write.writes():
                    with DECISIONS.open("a") as f:
                        f.write("\n" + entry)
                else:
                    log.info("sit under FFL_WRITES=0, not journaled: %s", entry)
            except OSError as e:
                log.warning("sit saved but not journaled: %s", e)
        # the cards carry every player field, so the call is re-made from them without an ESPN read
        sat = db.sat_ids(week)
        rows = espn_season.with_sits(data["cards"], sat)
        payload = {k: v for k, v in data.items() if k != "stale_since"}
        cache.put("roster", {**payload, "cards": season.roster_cards(rows, espn_season.load(), week)})
        cache.expire("week")  # the Matchup board's by-projection lineup leaves him out too
        return roster_()
    finally:
        _LINEUP.release()


def _live_for(week: int | None) -> bool:
    """Whether the feed's stored points belong to `week`. The state is tagged with the week the
    runner scored (live.State.week); after Monday night it keeps last week's totals until the
    next kickoff, while the week payload has already rolled over, so a plain "is this the
    current week" check would lay last week's points on this week's zeros."""
    return week is None or week == live.STATE.week


def _live_points(week: int | None) -> dict[int, float]:
    """The live overlay for `week`: only the week the feed scored, only once it has scored something."""
    if not live.STATE.points or not _live_for(week):
        return {}
    return live.STATE.points


def _live_games(week: int | None) -> dict[str, dict]:
    """Game clocks for the overlay, gated like the points."""
    if not live.STATE.games or not _live_for(week):
        return {}
    return live.STATE.games


def _live_stats(week: int | None) -> dict[int, dict]:
    """Raw stat rows for the overlay's stat lines, gated like the points."""
    if not live.STATE.stats or not _live_for(week):
        return {}
    return live.STATE.stats


def _scoring() -> list[dict] | None:
    """The league's stat table for the overlay's scoring breakdown; None (no breakdown) when the
    settings file cannot be read, rather than failing the page."""
    try:
        return load().get("scoring")
    except (Exception, SystemExit):  # noqa: BLE001 — load() exits when the file is missing
        return None


def _sigma() -> dict:
    """The win meter's spread per position (the fallback table when calibrate has not fitted one)."""
    return calibrate.load_sigma()[0]


FEED_ROWS = 5


def _plays(data: dict, limit: int | None = None) -> list[dict]:
    """The feed rows of one board's two teams, newest first, sided for that board. The runner's
    store is the week it last scored, which after Tuesday's rollover is still last week's, so a
    board of any other week has none."""
    runner = getattr(app.state, "runner", None)
    store = runner.store if runner else None
    if store is None or runner.week != data.get("week"):
        return []
    sides = [s for s in (data.get("me"), data.get("opp")) if s]
    rows = store.latest(limit, teams={s.get("team_id") for s in sides})
    return plays_mod.for_board(rows, sides[0].get("team_id")) if sides else []


def _week_payload(data: dict) -> dict:
    """A board with the live points laid on and its feed riding along, so one live tick refreshes both."""
    return {**live.overlay_week(data, _live_points(data["week"]), _live_games(data["week"]), _live_stats(data["week"]),
                                _scoring(), _sigma()),
            "plays": _plays(data, FEED_ROWS)}


def _board(n: int | None, team: int | None) -> dict:
    """My week's board, or with `team` the board of that team's game; each cached under its own key."""
    if team is None:
        return _serve("week:current" if n is None else f"week:{n}", lambda: espn_season.week_state(n))
    return _serve(f"week:{n or 'current'}:{team}", lambda: espn_season.week_state(n, team=team))


@app.get("/week")
def week_current():
    return _week_payload(_board(None, None))


@app.get("/plays")
def plays_(team: int | None = None):
    """Every feed row of the current week for one game, newest first (the /plays page): mine, or
    with `team` that team's. `me`/`opp` name the two sides the way the board does."""
    runner = getattr(app.state, "runner", None)
    data = _board(None, None) if team is None else _board(runner.week if runner and runner.week else None, team)
    club = lambda s: {k: s.get(k) for k in ("team_id", "team", "abbrev")} if s else None  # noqa: E731
    return {"week": data["week"], "live": season.is_live(), "mine": data.get("mine", True),
            "game_on": live.game_on([data.get("me"), data.get("opp")], _live_games(data["week"])),
            "me": club(data.get("me")), "opp": club(data.get("opp")), "plays": _plays(data),
            "stale_since": data.get("stale_since")}


@app.get("/week/{n}")
def week(n: int, team: int | None = None):
    """My week's board, or with `team` the board of that team's game (a League scoreboard tile)."""
    return _week_payload(_board(n, team))


@app.get("/team/{team_id}")
def team(team_id: int):
    """Any fantasy team's roster for the current week (the team page), live points laid on."""
    data = _serve(f"team:{team_id}", lambda: espn_season.team_state(team_id))
    wk = data["week"]
    return {**data, "side": live.overlay_side(data["side"], _live_points(wk), _live_games(wk), _live_stats(wk), _scoring())}


PLAYER_TTL = 600.0  # a game log moves once a week; the live week rides on the feed, not this cache


@app.get("/player/{player_id}")
def player(player_id: int):
    """One player's game log, this season and last (the season modal), the live week from the feed."""
    data = _serve(f"player:{player_id}", lambda: espn_season.player_card(player_id), PLAYER_TTL)
    wk = data["current_week"]
    row, scoring = _live_stats(wk).get(player_id), _scoring()
    if row and scoring:
        data = player_card.with_live(data, wk, row, scoring, _live_games(wk).get(data["team"]))
    return data


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


@app.get("/players")
def players(pos: Literal["QB", "RB", "WR", "TE", "D/ST", "K"] | None = None, q: str | None = None, fa: bool = True):
    # free agents at a position by default; fa=0 adds every rostered player there. A search spans
    # every position and both halves whatever `fa` says: a lookup must not miss a rostered man.
    q = (q or "").strip() or None
    if q is None and pos is None:
        raise HTTPException(422, "pos or q is required")
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    rostered = data.get("rostered", {})  # absent from a pool cached before it existed
    # read on every request, as /roster attaches its outlooks: a finished write shows on the next load
    written = outlook.written(data["week"])
    if q:
        pos = None
        found = [r for half in (data["positions"], rostered) for rows in half.values() for r in rows
                 if season.name_matches(r, q)]
    else:
        found = data["positions"].get(pos, []) + ([] if fa else rostered.get(pos, []))
    rows = [{"owner": None, "mine": False, **r, "has_outlook": not r.get("owner") and r["player_id"] in written}
            for r in season.rank_players(found)]
    return {"week": data["week"], "pos": pos, "q": q, "fa": fa, "rows": rows,
            "roster": data.get("roster", []),  # my players, for the row's comparison dropdown
            # the week ROS starts from, and whether this week's games are all played (no week term)
            "ros_week": data.get("ros_week", data["week"]), "week_over": data.get("week_over", False),
            "ros_source": data.get("ros_source", "season"), "stale_since": data["stale_since"]}


@app.get("/league")
def league_(week: int | None = None):
    data = _serve(f"league:{week or 'current'}", lambda: espn_season.league_state(week))
    return live.overlay_league(data, _live_points(data["week"]), _live_games(data["week"]), _sigma())


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


@app.get("/transactions")
def transactions():
    """The league's adds, drops and trades as moves (an add beside the drop it made room with)."""
    return _serve("transactions", espn_season.transactions_state)


@app.get("/player/{player_id}/transactions")
def player_transactions(player_id: int):
    """One player's moves this season (the season modal's Transactions section), off the cached
    /transactions payload: no ESPN call of its own."""
    data = _serve("transactions", espn_season.transactions_state)
    history = season.player_transactions(data["moves"], player_id)
    try:  # the draft row is the oldest; a draft that cannot be read leaves the moves standing
        picks = cache.get_or_build("draft_picks", lambda: {"picks": espn_season.draft_picks()}, DRAFT_TTL)[0]["picks"]
        pick = season.draft_entry(picks, player_id, {c["team_id"]: c for c in data.get("teams", [])})
    except Exception as e:  # noqa: BLE001 — HTTPException from the cache included
        print(f"draft pick unavailable for {player_id}: {e}")
        pick = None
    return {"history": history + ([pick] if pick else []), "stale_since": data["stale_since"]}


DRAFT_TTL = 24 * 3600.0  # the draft is over; the record does not move


@app.get("/roster")
def roster_():
    data = _serve("roster", lambda: espn_season.roster_state(None))
    # gated on the payload's week, as /week is: after the rollover the feed still holds last week's
    data = live.overlay_roster(data, _live_points(data["week"]), _live_games(data["week"]), _live_stats(data["week"]), _scoring())
    # attach on every request, not inside the cached build: a finished generation run must show
    # on the next load, not after the TTL
    return {**data, "cards": outlook.attach(data["cards"], outlook.load(data["week"]), outlook.writing()),
            "outlook_run": {**outlook.LAST, "running": outlook.running()}}


def _roster_card(player_id: int) -> tuple[dict, int]:
    """His card in the cached roster and its week; 404 once he is off the roster."""
    data = _serve("roster", lambda: espn_season.roster_state(None))
    for c in data["cards"]:
        if c["player_id"] == player_id:
            return c, data["week"]
    raise HTTPException(status_code=404, detail=f"player {player_id} is not on the roster")


@app.get("/outlook/{player_id:int}")
def card_outlook(player_id: int):
    card, week = _roster_card(player_id)
    return outlook.roster_entry(card, week)


@app.post("/outlook/{player_id:int}", status_code=202)
def card_outlook_write(player_id: int):
    """One Roster card's outlook, rewritten in the background whether or not his facts moved; the card
    polls the GET until `outlook_running` clears. 409 while the full run holds the Roster file."""
    card, week = _roster_card(player_id)
    try:
        outlook.start_one(card, week, run=outlook.run_claude, kind=outlook.ROSTER)
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"started": True}


@app.post("/outlook/refresh", status_code=202)
def outlook_refresh():
    try:
        state = espn_season.roster_state(None)
    except Exception as e:  # noqa: BLE001 — a source failure is a 502, not a bare traceback
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    try:
        outlook.start(state["cards"], state["week"], run=outlook.run_claude)
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    # pending needs the news context, which is fetched in the run's thread; the page reads the count from outlook_run
    return {"started": True, "pending": None}


def _free_agent(player_id: int) -> tuple[dict, int]:
    """His row in the cached pool and its week; 404 once he is off the wire."""
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    for rows in data["positions"].values():
        for r in rows:
            if r["player_id"] == player_id:
                return r, data["week"]
    raise HTTPException(status_code=404, detail=f"player {player_id} is not a free agent")


@app.get("/waivers/outlook/{player_id}")
def waiver_outlook(player_id: int):
    row, week = _free_agent(player_id)
    return outlook.waiver_entry(row, week)


@app.post("/waivers/outlook/{player_id}", status_code=202)
def waiver_outlook_write(player_id: int):
    """One free agent's outlook, written in the background (a minute or two of Opus with web search);
    the dialog polls the GET until `running` clears."""
    row, week = _free_agent(player_id)
    try:
        outlook.start_one(row, week, run=outlook.run_claude)
    except outlook.Busy as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"started": True}


def sync_all() -> dict:
    # espn_api caches current_week and the records in League.__init__; a months-old League would
    # make sync_results upsert stale scores for the weeks it still knows about.
    espn_season.refresh_league()
    lg = espn_season.fresh_league()
    settings = load()
    sync_transactions(lg)
    sync_results(lg, settings)
    return {"transactions": True, "results": True}


@app.post("/transactions/sync")
def transactions_sync():
    """The hourly tick (launchd/com.jonulak.ffl-transactions): ESPN's activity feed into
    the DB's transactions table, so the record keeps up with no page open. Concurrent syncs are
    safe: the table's (season, id) key dedupes."""
    try:
        new = sync_transactions(espn_season.fresh_league())
    except Exception as e:  # noqa: BLE001 — curl's non-zero exit on the 502 is the log line
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    if new:
        cache.expire("transactions")
    return {"new": len(new)}


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
    if cbs._CACHE:  # same shape, same reason
        cbs._CACHE[0] = 0.0
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
