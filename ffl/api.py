"""FastAPI glue for the season UI: ESPN/Sleeper state behind a short cache, a few writes.

    uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300

Loopback only; Next.js on :3300 proxies /api/* here. Stale payloads are kept past the TTL and
served, flagged, when a source is down — never a blank page on Sunday.

Only these endpoints write anything: POST /refresh runs the journal sync, POST /outlook/refresh starts a background run that writes
data/outlook/week-<n>.json, POST /waivers/outlook/{id} writes one free agent's into data/outlook/waivers-week-<n>.json,
POST /claims saves the week's claim plan to journal/waivers/week-<n>.json (never touches ESPN), and POST /claims/submit is
the one route that writes to ESPN (instant free-agent adds + waiver claims, through ffl/espn_write.py) and journals it.
Every GET is read-only.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from . import calibrate, cbs, espn_season, espn_write, live, outlook, player_card, season
from . import claims as claims_mod
from . import plays as plays_mod
from .config import JOURNAL
from .journal import sync_results, sync_transactions
from .settings import load

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

CLAIMS_DIR = JOURNAL / "waivers"
DECISIONS = JOURNAL / "DECISIONS.md"
_SUBMIT = threading.Lock()  # one submit at a time: it cancels and reposts ESPN's whole claim list


class GroupIn(BaseModel):
    targets: list[int]
    cuts: list[int]
    open: int = 0


class PlanIn(BaseModel):
    groups: list[GroupIn]


class SubmitIn(BaseModel):
    replace_outside: bool = False


def _roster_size() -> int:
    return int(load()["roster_size"])


def _plan_path(week):
    return CLAIMS_DIR / f"week-{week}.json"


def _load_plan(week):
    p = _plan_path(week)
    return json.loads(p.read_text()) if p.exists() else {"week": week, "groups": [], "submits": []}


def _save_plan(plan):
    CLAIMS_DIR.mkdir(parents=True, exist_ok=True)
    _plan_path(plan["week"]).write_text(json.dumps(plan, indent=1) + "\n")


def _pool_rows(data):
    return {r["player_id"]: r for rows in data["positions"].values() for r in rows}


def _status(data):
    return {i: r.get("status") for i, r in _pool_rows(data).items() if r.get("status")}


def _players(groups, data):
    rows, roster = _pool_rows(data), {r["player_id"]: r for r in data.get("roster", [])}
    want = {i for g in groups for i in g["targets"] + g["cuts"]} | set(roster)
    keys = ("name", "pos", "team", "add", "ros_adj", "slot", "status", "waiver_date", "droppable", "locked")
    return {str(i): {k: (rows.get(i) or roster.get(i)).get(k) for k in keys} for i in want if rows.get(i) or roster.get(i)}


def _claims_payload(plan, data):
    roster, status = data.get("roster", []), _status(data)
    last = plan["submits"][-1] if plan["submits"] else None
    ids = set(last["ids"]) if last else set()
    now, expansion = claims_mod.plan_now(plan["groups"], status), claims_mod.expand(plan["groups"], status)
    try:
        pending, perr = espn_write.pending_claims(), None
    except espn_write.WriteError as e:
        pending, perr = [], ("ESPN login expired, refresh .env" if e.expired else str(e))
    key = lambda ms: [(m["add"], m["drop"]) for m in ms]  # noqa: E731
    return {"week": plan["week"], "groups": plan["groups"], "now": now, "claims": expansion,
            "errors": claims_mod.validate(plan["groups"], roster, set(_pool_rows(data)), _roster_size(), status),
            "open_spots": claims_mod.open_spots(roster, _roster_size()),
            "status": {str(i): s for i, s in status.items()},
            "pending": [{**c, "from_app": c["id"] in ids} for c in pending], "pending_error": perr,
            "last_submit": last,
            # claims only: instant adds already happened, and the plan's free agents are on the roster after a refresh
            "edited_since_submit": bool(last) and key(last["claims"]) != key(expansion),
            "players": _players(plan["groups"], data)}


@app.get("/claims")
def claims_get():
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    return _claims_payload(_load_plan(data["week"]), data)


@app.post("/claims")
def claims_save(body: PlanIn):
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    plan = {**_load_plan(data["week"]), "groups": [g.model_dump() for g in body.groups]}
    _save_plan(plan)
    return _claims_payload(plan, data)


@app.post("/claims/submit")
def claims_submit(body: SubmitIn):
    if not _SUBMIT.acquire(blocking=False):
        raise HTTPException(409, "a submit is already running")
    try:
        data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
        plan, roster = _load_plan(data["week"]), data.get("roster", [])
        # fresh statuses: the pool is cached, and the Wednesday run turns WAIVERS into FREEAGENT
        try:
            status = {i: v["status"] for i, v in espn_season.pool_status().items() if v.get("status")}
        except Exception:  # noqa: BLE001 — cached statuses; unknown counts as WAIVERS
            status = _status(data)
        errs = claims_mod.validate(plan["groups"], roster, set(_pool_rows(data)), _roster_size(), status)
        if errs:
            raise HTTPException(422, {"errors": errs})
        try:
            pending = espn_write.pending_claims()
        except espn_write.WriteError as e:
            raise HTTPException(502, {"source": "espn", "error": "ESPN login expired, refresh .env" if e.expired else str(e)})
        last = plan["submits"][-1] if plan["submits"] else None
        wk = data["week"]  # ESPN's current scoring period, which is what its write bodies want
        w = SimpleNamespace(pending_claims=espn_write.pending_claims,
                            cancel_claim=lambda i: espn_write.cancel_claim(i, wk),
                            submit_claim=lambda a, d: espn_write.submit_claim(a, d, wk),
                            add_free_agent=lambda a, d: espn_write.add_free_agent(a, d, wk))
        res = claims_mod.run_submit(plan["groups"], status, pending, set(last["ids"]) if last else set(), body.replace_outside, w)
        strip = lambda m: {k: m[k] for k in ("group", "add", "drop")}  # noqa: E731
        # a failed run still records the ids that did land, so the next submit cancels them
        plan["submits"].append({"at": datetime.now().isoformat(timespec="seconds"), "now": [strip(m) for m in res["now"]],
                                "claims": [strip(c) for c in res["sent"]], "ids": [c["id"] for c in res["sent"]]})
        _save_plan(plan)
        if res["now"] or res["sent"]:
            names = {i: r["name"] for i, r in _pool_rows(data).items()} | {r["player_id"]: r["name"] for r in roster}
            with DECISIONS.open("a") as f:
                f.write("\n" + claims_mod.decision_entry(plan["groups"], names, datetime.now().strftime("%Y-%m-%d"), status))
        if res["now"]:
            cache.expire_all()  # the roster changed: the pages must not show the dropped player
        return res
    finally:
        _SUBMIT.release()


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


@app.get("/waivers")
def waivers(pos: Literal["QB", "RB", "WR", "TE", "D/ST", "K"] | None = None, q: str | None = None):
    # a search spans every position's pool and ignores `pos`; without one, the position is required
    q = (q or "").strip() or None
    if q is None and pos is None:
        raise HTTPException(422, "pos or q is required")
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    # read on every request, as /roster attaches its outlooks: a finished write shows on the next load
    written = outlook.written(data["week"])
    if q:
        pos = None
        found = [r for rows in data["positions"].values() for r in rows if season.name_matches(r, q)]
        found.sort(key=lambda r: -r["add"])
    else:
        found = data["positions"].get(pos, [])
    rows = [{**r, "has_outlook": r["player_id"] in written} for r in found]
    return {"week": data["week"], "pos": pos, "q": q, "rows": rows,
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
