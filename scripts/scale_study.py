"""Scale and caching study (#123): count every outside call, time every page build and job step, and size every
cache, in-process against a study database: a copy of production with two more users joined to the league measured (issue #123).

Run from a worktree with a study database (scripts/devdb.sh study --from <url>, then ffl.leagues user add / join for B and C):
  uv run python scripts/scale_study.py [league-key] > out.txt   (writes calls.json beside the output)
"""
from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import threading
import time
import tracemalloc
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

sys.path.insert(0, os.getcwd())  # the worktree: `uv run python <scratchpad file>` puts only the file's folder on the path
os.environ.setdefault("FFL_LIVE", "0")
os.environ.setdefault("FFL_NOTIFY", "0")
os.environ.setdefault("FFL_WRITES", "0")

import requests  # noqa: E402

# ---------------------------------------------------------------- the outside-call counter
CALLS: list[dict] = []
_LOCK = threading.Lock()
_orig_request = requests.sessions.Session.request


def _ffl_frames() -> str:
    """The innermost two ffl/ frames (file:function) of the calling stack, outside this file and espn_api."""
    out = []
    for f in traceback.extract_stack()[:-2][::-1]:
        fn = f.filename
        if "/ffl/" in fn and "site-packages" not in fn and "scratchpad" not in fn:
            out.append(f"{fn.split('/ffl/')[-1]}:{f.name}")
            if len(out) == 2:
                break
    return " < ".join(out)


def _counted_request(self, method, url, **kw):
    t0 = time.time()
    r = _orig_request(self, method, url, **kw)
    u = urlsplit(url)
    params = kw.get("params") or {}
    views = params.get("view") if isinstance(params, dict) else [v for k, v in params if k == "view"] if params else None
    if isinstance(views, str):
        views = [views]
    path = u.path
    for seg in path.split("/"):
        if seg.isdigit() and len(seg) > 4:
            path = path.replace(seg, "<id>")
    league = next((seg for seg in u.path.split("/") if seg.isdigit() and len(seg) >= 9), None)
    with _LOCK:
        CALLS.append({"host": u.netloc, "path": path, "views": ",".join(views or []), "league": league,
                      "ms": round((time.time() - t0) * 1000), "bytes": len(r.content or b""),
                      "by": _ffl_frames(), "thread": threading.current_thread().name, "stage": STAGE[0]})
    return r


requests.sessions.Session.request = _counted_request
STAGE = ["import"]

# ---------------------------------------------------------------- memory helpers


def rss_mb() -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    return round(int(out.strip()) / 1024, 1)


def deep_size(obj, seen: set | None = None) -> int:
    """sys.getsizeof over containers and object __dict__s, each object once."""
    seen = set() if seen is None else seen
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    n = sys.getsizeof(obj)
    if isinstance(obj, dict):
        n += sum(deep_size(k, seen) + deep_size(v, seen) for k, v in list(obj.items()))
    elif isinstance(obj, (list, tuple, set, frozenset)):
        n += sum(deep_size(x, seen) for x in obj)
    elif hasattr(obj, "__dict__"):
        n += deep_size(vars(obj), seen)
    elif hasattr(obj, "__slots__"):
        n += sum(deep_size(getattr(obj, s), seen) for s in obj.__slots__ if hasattr(obj, s))
    return n


def mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB" if n > 524_288 else f"{n / 1024:.0f} kB"


# ---------------------------------------------------------------- reporting
REPORT: list[str] = []


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    REPORT.append(s)


def stage(name: str):
    STAGE[0] = name
    say(f"\n## {name}   (rss {rss_mb()} MB)")


def calls_since(mark: int) -> list[dict]:
    with _LOCK:
        return CALLS[mark:]


def summarize(rows: list[dict], label: str) -> None:
    by = Counter()
    ms = Counter()
    byts = Counter()
    for c in rows:
        k = (c["host"], c["path"], c["views"], c["league"])
        by[k] += 1
        ms[k] += c["ms"]
        byts[k] += c["bytes"]
    say(f"  {label}: {len(rows)} calls, {sum(c['ms'] for c in rows)/1000:.1f} s of fetch, {mb(sum(c['bytes'] for c in rows))}")
    for k, n in by.most_common():
        say(f"    {n:3d}x {k[0]} {k[1]} view={k[2] or '-'} league={k[3] or '-'}  {ms[k]} ms  {mb(byts[k])}")
    callers = Counter(c["by"] for c in rows)
    for who, n in callers.most_common(8):
        say(f"      {n:3d} from {who}")


def timed(label: str, fn):
    mark = len(CALLS)
    t0 = time.time()
    err = None
    try:
        out = fn()
    except BaseException as e:  # noqa: BLE001 — the study reports failures as rows
        out, err = None, f"{type(e).__name__}: {str(e)[:160]}"
    dt = time.time() - t0
    rows = calls_since(mark)
    say(f"- {label}: {dt:.2f} s, {len(rows)} outside calls" + (f"  FAILED {err}" if err else ""))
    return out, rows


# ---------------------------------------------------------------- the study
tracemalloc.start(1)
stage("import ffl.api")
from ffl import api, db, espn_season, leagues, live, notify, nfl, plays, sources, config, espn_write  # noqa: E402
from ffl.newsdesk import pipeline, collect  # noqa: E402
from ffl import outlook  # noqa: E402

outlook.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("claude -p stubbed"))  # never call the CLI here
say("rss after import:", rss_mb(), "MB")

PUB = sys.argv[1] if len(sys.argv) > 1 else "espn:950458410"  # a league readable from this checkout (a public one needs no cookies)
members = leagues.members(PUB)
say("members of the public league:", [(m.user_id, m.team_id) for m in members])
A, B, C = members[0], members[1], members[2]
PRIV = [r for r in leagues.joined() if r.key != PUB]
say("other leagues (private, unreadable in this copy):", [r.key for r in PRIV])

stage("boot: load stored pages for every membership (lifespan does this)")
for m in leagues.memberships():
    api.cache_for(m)
say("caches open:", len(api.caches), " rss", rss_mb())

PAGES = [("week:current", lambda r: espn_season.week_state(ref=r)),
         ("roster", lambda r: espn_season.roster_state(ref=r)),
         ("league:current", lambda r: espn_season.league_state(ref=r)),
         ("waivers", lambda r: espn_season.free_agent_pool(ref=r)),
         ("transactions", lambda r: espn_season.transactions_state(ref=r)),
         ("team:3", lambda r: espn_season.team_state(3, ref=r))]
SIZES: dict[str, int] = {}


def build_pages(ref, label):
    stage(f"{label}: the four warm pages + transactions + a team, cold caches for this member")
    for key, fn in PAGES:
        c = api.cache_for(ref)
        c.drop(key)
        payload, rows = timed(f"{key}", lambda: c.get_or_build(key, lambda: fn(ref))[0])
        if payload is not None:
            SIZES[key] = len(json.dumps(payload))
            say(f"    payload {mb(SIZES[key])}")
        summarize(rows, "  calls")


build_pages(A, "member A (owner, team 1)")
build_pages(B, "member B (team 2): what a second member of the same league costs")
build_pages(C, "member C (co-manager of team 1): same team as A")

stage("warm hits: the same pages again within TTL (should be 0 calls)")
for key, fn in PAGES[:4]:
    timed(key, lambda: api.cache_for(A).get_or_build(key, lambda: fn(A))[0])

stage("player cards (PLAYER_TTL 600 s, one per player ever opened)")
pool = api.cache_for(A).peek("waivers") or {}
pids = [r["player_id"] for r in (pool.get("positions") or {}).get("RB", [])[:3]]
for pid in pids:
    payload, rows = timed(f"player:{pid}", lambda: api.cache_for(A).get_or_build(f"player:{pid}", lambda: espn_season.player_card(pid, ref=A), api.PLAYER_TTL)[0])
    if payload:
        say(f"    payload {mb(len(json.dumps(payload)))}")
    summarize(rows, "  calls")

stage("past weeks: week:N and league:N for the League picker (finished weeks, from the box_scores table)")
for w in (5, 6, 7):
    for key, fn in ((f"week:{w}", lambda r, w=w: espn_season.week_state(w, ref=r)), (f"league:{w}", lambda r, w=w: espn_season.league_state(w, ref=r))):
        payload, rows = timed(key, lambda: api.cache_for(A).get_or_build(key, lambda: fn(A))[0])
        summarize(rows, "  calls")

stage("notify.tick(): every league's moves/injuries/waivers/projections, then sources + flush")
out, rows = timed("notify.tick", notify.tick)
say("  ", json.dumps(out)[:600])
summarize(rows, "  calls")

stage("/notify/tick's extra per-league passes")
out, rows = timed("rollover_pass", lambda: api.rollover_pass(ref=A)); summarize(rows, "  calls")
out, rows = timed("correction_pass", lambda: api.correction_pass(ref=A)); say("  ", json.dumps(out)[:300]); summarize(rows, "  calls")
# a watchlist for B and C too, so the watch pass has work per member
for m in (A, B, C):
    for pid in pids[:2]:
        db.put_watch(pid, True, ref=m)
    db.put_league_state("watch_state", {}, ref=m)
out, rows = timed("watch_members (A, B, C each watching 2)", lambda: api.watch_members(ref=A)); say("  ", json.dumps(out)[:400]); summarize(rows, "  calls")
out, rows = timed("pipeline.owners (news desk, per league per run)", lambda: pipeline.owners(ref=A)); summarize(rows, "  calls")
out, rows = timed("collect._rotowire_ids (every league)", lambda: len(collect._rotowire_ids())); say("   ids:", out); summarize(rows, "  calls")

stage("a league move: on_new_rows drops MOVE_KEYS in every open member cache and rebuilds them behind")
mark = len(CALLS)
t0 = time.time()
api.on_new_rows([], ref=A)
for c in api.caches_for_league(A):
    c.join(120)
rows = calls_since(mark)
say(f"- rewarm after a move for {len(api.caches_for_league(A))} member caches: {time.time()-t0:.1f} s, {len(rows)} calls")
summarize(rows, "  calls")

stage("pregame pull: expire everything of the league, rebuild every member's expired pages")
kicks = [k for ks in espn_season.kickoffs_by_week(espn_season.fresh_league(A)).values() for k in ks]
future = sorted(k for k in kicks if k > datetime.now(timezone.utc))
say("  next kickoff:", future[0].isoformat() if future else None)
db.put_league_state("pregame_pulls", [], ref=A)
mark = len(CALLS)
t0 = time.time()
pulled = api.pregame_pull(now=future[0] - timedelta(minutes=30), ref=A) if future else []
for c in api.caches_for_league(A):
    c.join(300)
rows = calls_since(mark)
say(f"- pregame pull ({pulled}) rebuilt {len(api.caches_for_league(A))} member caches: {time.time()-t0:.1f} s, {len(rows)} calls")
summarize(rows, "  calls")

stage("sizes: every module-level cache, deep sys.getsizeof (one league, three members)")
gc.collect()
inventory = {
    "api.caches (page payloads, all members)": {k: {kk: e.payload for kk, e in c._d.items()} for k, c in api.caches.items()},
    "config._LEAGUES (espn-api League objects)": config._LEAGUES,
    "espn_season._BOXES (box scores by week)": espn_season._BOXES,
    "espn_season._SL (Sleeper raw weekly rows)": espn_season._SL,
    "espn_season._SL_SCORED": espn_season._SL_SCORED,
    "espn_season._SNAPS": espn_season._SNAPS,
    "espn_season._SEASON_CACHE (ROS lookup per league)": espn_season._SEASON_CACHE,
    "espn_season._INJ": espn_season._INJ,
    "espn_season._GAP_GAMES": espn_season._GAP_GAMES,
    "espn_season._RANKS/_POS_RANKS/_PAST_STATS": [espn_season._RANKS, espn_season._POS_RANKS, espn_season._PAST_STATS],
    "espn_season schedule docs (_SCHED/_HOME/_BYE/_KICKS/_PAST_SCHED)": [espn_season._SCHED, espn_season._HOME, espn_season._BYE, espn_season._KICKS, espn_season._PAST_SCHED],
    "sources.Cached entries (CBS, Sharks, FleaFlicker)": [getattr(c, "_d", None) or vars(c) for c in sources._ALL],
    "nfl._BOARDS/_DRIVES": [nfl._BOARDS._d, nfl._DRIVES._d],
    "espn_write._READ": espn_write._READ,
}
for name, obj in inventory.items():
    say(f"  {mb(deep_size(obj)):>10}  {name}")
for k, c in api.caches.items():
    say(f"    {mb(deep_size({kk: e.payload for kk, e in c._d.items()})):>10}  pages of user {k[0]} in {k[1]}: {sorted(c._d)}")
for (lk, w), (exp, lg, boxes) in espn_season._BOXES.items():
    say(f"    {mb(deep_size(boxes)):>10}  _BOXES {lk} week {w} ({'final' if exp == float('inf') else 'ttl'})")
for w, (exp, rows_) in espn_season._SL.items():
    say(f"    {mb(deep_size(rows_)):>10}  _SL week {w}: {len(rows_)} rows")

stage("plays.Store for the live week (one per league lane in the runner)")
wk = espn_season.fresh_league(A).current_week
st, _ = timed("plays.Store", lambda: plays.Store(wk, ref=A))
if st:
    say(f"  rows {len(st.rows)} book {len(st.book)} called {len(st.called)}  deep {mb(deep_size(st))}")
for r in PRIV:
    st2, _ = timed(f"plays.Store {r.key}", lambda: plays.Store(wk, ref=r))
    if st2:
        say(f"  rows {len(st2.rows)} book {len(st2.book)}  deep {mb(deep_size(st2))}")

stage("tracemalloc: Python heap by file (top 15)")
snap = tracemalloc.take_snapshot()
for s in snap.statistics("filename")[:15]:
    say(f"  {mb(s.size):>10}  {s.traceback[0].filename.split('site-packages/')[-1].split('/ffl/')[-1]}")
say("total traced:", mb(tracemalloc.get_traced_memory()[0]), " rss:", rss_mb(), "MB")

stage("payload sizes (bytes of JSON, what a /live tick makes each open page refetch)")
for k, n in sorted(SIZES.items()):
    say(f"  {mb(n):>10}  {k}")

stage("done")
say("total outside calls:", len(CALLS))
json.dump(CALLS, open("calls.json", "w"), indent=0)
