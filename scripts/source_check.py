"""Every outside source the app reads, asked once from wherever this runs (the Mac, or the AWS box as `ffl`).

    uv run python scripts/source_check.py [--only SUBSTR ...]

One line per check, `OK name detail`, `FAIL name error`, or `SKIP name disabled (FFL_SOURCES_OFF=<token>)` for a source this
host switched off; exit 1 when any check fails (a SKIP is not a failure). Read-only: each check
calls the source's own fetch function, never a copy of its request, and only ones that write nothing (the
cached wrappers that persist to `source_cache` / `source_status` are skipped for the plain fetch beneath them).
No ESPN write, no DB write (PGOPTIONS makes every session read-only unless the caller set PGOPTIONS), no push, no
news-desk run. The DB is read for the league registry and settings only.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

# before any ffl import opens a connection: every DB session this process opens is read-only, so a write would error
os.environ.setdefault("PGOPTIONS", "-c default_transaction_read_only=on")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repo, when run as a file

Check = tuple[str, Callable[[], str]]
WORKERS = 8


# ---- the runner (pure over the checks; tested) ------------------------------------------------------------------

class Skip(Exception):
    """A check of a source this host has switched off (FFL_SOURCES_OFF): reported, never a FAIL."""


def _off(name: str) -> None:
    from ffl import config
    if config.source_off(name):
        raise Skip(f"disabled (FFL_SOURCES_OFF={name})")


def _err(e: BaseException) -> str:
    return " ".join(f"{type(e).__name__}: {e}".split())[:300]


def run_one(name: str, fn: Callable[[], str]) -> tuple[str, str, str]:
    """(name, "OK" | "FAIL" | "SKIP", detail)."""
    try:
        return name, "OK", str(fn() or "")
    except Skip as e:
        return name, "SKIP", str(e)
    except BaseException as e:  # noqa: BLE001 — SystemExit too (adp.fetch, leagues.get raise it)
        if isinstance(e, KeyboardInterrupt):
            raise
        return name, "FAIL", _err(e)


def run(checks: list[Check], workers: int = WORKERS) -> list[tuple[str, str, str]]:
    """Every check, in order of the list, `workers` at a time."""
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda c: run_one(*c), checks))


def line(result: tuple[str, str, str]) -> str:
    name, status, detail = result
    return f"{status} {name} {detail}".rstrip()


def report(results: list[tuple[str, str, str]]) -> int:
    for r in results:
        print(line(r), flush=True)
    n = {k: sum(1 for _, s, _ in results if s == k) for k in ("OK", "FAIL", "SKIP")}
    print(f"-- {n['OK']} OK, {n['FAIL']} FAIL, {n['SKIP']} SKIP")
    return 1 if n["FAIL"] else 0


def select(checks: list[Check], only: list[str] | None) -> list[Check]:
    return [c for c in checks if not only or any(s in c[0] for s in only)]


# ---- the checks ---------------------------------------------------------------------------------------------------

_MEMO: dict = {}
_MEMO_LOCK = threading.Lock()


def _memo(key, fn):
    with _MEMO_LOCK:
        if key not in _MEMO:
            _MEMO[key] = fn()
        return _MEMO[key]


def _need(cond, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def _scoreboard() -> dict:
    from ffl import nfl
    return _memo("sb", lambda: nfl.scoreboard(None)[0])  # nfl's TtlCache is memory only


def _week() -> int:
    try:
        return int(_scoreboard()["week"]) or 8
    except Exception:  # noqa: BLE001 — the scoreboard check reports its own failure
        return 8


def espn_private(ref, view: str) -> Callable[[], str]:
    def check() -> str:
        from ffl import config
        doc = config.raw_league_get(view, ref=ref)
        teams = doc.get("teams") or []
        _need(teams, f"no teams in {view}")
        if view == "mRoster":
            entries = [e for t in teams for e in ((t.get("roster") or {}).get("entries") or [])]
            _need(entries, "no roster entries")
            _MEMO.setdefault("pid", next((e["playerId"] for e in entries if (e.get("playerId") or 0) > 0), None))
            return f"{len(teams)} teams, {len(entries)} roster entries"
        return f"{len(teams)} teams, {len(doc.get('schedule') or [])} matchups, period {doc.get('scoringPeriodId')}"
    return check


def espn_injuries() -> str:
    from ffl import espn_season
    data = espn_season.fetch_injuries()  # swallows a RequestException into {}: the real error is not visible here
    _need(data, "empty (fetch_injuries hides the HTTP error; a failed read returns {})")
    return f"{len(data)} players"


def espn_pro_schedule() -> str:
    from ffl import config
    doc = config.league().espn_request.get_pro_schedule()  # what espn_season._schedule_doc reads, uncached
    teams = (doc.get("settings") or {}).get("proTeams") or []
    _need(teams, "no proTeams")
    return f"{len(teams)} pro teams"


def espn_players_index() -> str:
    from ffl.newsdesk import players
    rows = players._public_players()
    _need(rows, "no players")
    return f"{len(rows)} players"


def fastcast() -> str:
    """Host lookup, socket, subscribe, and the checkpoint the H frame names, through Client itself: on_doc stops
    the loop at the first document, and the `with connect` closes the socket."""
    from ffl.fastcast import Client
    stop = threading.Event()
    got: dict = {}
    c = Client(on_doc=lambda d: (got.update(doc=d), stop.set()))
    out: dict = {}

    def go():
        try:
            c.run_once(stop)
        except BaseException as e:  # noqa: BLE001
            out["e"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(45)
    if t.is_alive():
        stop.set()
        t.join(10)
        raise TimeoutError("no checkpoint within 45 s")
    if "e" in out:
        raise out["e"]
    doc = got.get("doc") or {}
    _need(doc, "no document")
    return f"checkpoint mid {c.mid}, {len(doc.get('events') or doc)} top-level entries"


def nfl_scoreboard() -> str:
    sb = _scoreboard()
    _need(sb["games"], "no games")
    return f"week {sb['week']}, {len(sb['games'])} games"


def nfl_drives() -> str:
    from ffl import nfl
    games = _scoreboard()["games"]
    g = next((g for g in games if g["status"] in ("in", "post")), None)
    if g is None:  # nothing played this week yet: last week's
        g = next((g for g in nfl.scoreboard(_week() - 1)[0]["games"] if g["status"] == "post"), None)
    _need(g, "no played game to read")
    drives, stale = nfl.drives(g["event_id"], g["status"], {})
    _need(drives and not stale, f"no drives for event {g['event_id']}")
    return f"event {g['event_id']}: {len(drives)} drives, {sum(len(d['plays']) for d in drives)} plays"


def sleeper_week() -> str:
    from ffl import espn_season
    rows = espn_season.fetch_sleeper_rows(_week())
    _need(rows, "no rows")
    return f"week {_week()}: {len(rows)} rows"


def sleeper_stats() -> str:
    from ffl import espn_season
    w = max(1, _week() - 1)
    rows = espn_season.fetch_sleeper_stats(w)
    _need(rows, "no rows")
    return f"week {w}: {len(rows)} rows"


def sleeper_season() -> str:
    from ffl import sleeper
    from ffl.config import SEASON
    rows = sleeper.fetch_raw(SEASON)
    _need(rows, "no rows")
    return f"{SEASON}: {len(rows)} rows"


def sleeper_players() -> str:
    from ffl import news
    chart = news.fetch_depth()  # swallows the HTTP error into {}
    _need(chart, "empty (fetch_depth hides the HTTP error; a failed read returns {})")
    return f"{len(chart)} clubs' depth charts"


def _counts(data: dict) -> str:
    from ffl import sources
    return ", ".join(f"{p} {n}" for p, n in sources.check(data).items())  # pure: raises Thin on a thin scrape


def cbs_ros() -> str:
    from ffl.sources import cbs
    return _counts(cbs._pages(cbs.URL))  # not fetch_ros: Cached writes source_cache / source_status


def cbs_week() -> str:
    from ffl.sources import cbs
    w = _week()
    return f"week {w}: " + _counts(cbs._pages(cbs.WEEK_URL, week=w))


def sharks() -> str:
    _off("sharks")
    from ffl.sources import sharks
    w = _week()
    return f"week {w}: " + _counts(sharks._fetch(sharks.segment(w)))  # not week_stats (Cached)


def fleaflicker() -> str:
    from ffl.sources import Idle, fleaflicker
    w = _week()
    parts = {}
    for pos in fleaflicker.POS_CODE:
        try:
            got = fleaflicker._position(pos, w)  # not fetch_week (Cached)
        except Idle as e:
            got = None
            parts[pos] = f"idle ({e})"
            continue
        parts[pos] = "idle" if got is None else str(len(got))
    _need(any(v.isdigit() and int(v) > 0 for v in parts.values()) or all("idle" in v for v in parts.values()),
          f"nothing parsed: {parts}")
    return f"week {w}: " + ", ".join(f"{p} {n}" for p, n in parts.items())


def ffc_adp(fmt: str, teams: int, year: int) -> Callable[[], str]:
    def check() -> str:
        from ffl import adp
        df = adp.fetch(fmt, teams, year)  # not store(): that writes adp_feeds
        _need(len(df), "no players")
        return f"{fmt} {teams} teams {year}: {len(df)} players"
    return check


def rotowire() -> str:
    from ffl.newsdesk import collect
    pid = _MEMO.get("pid")
    _need(pid, "no player id (the mRoster check failed)")
    doc = collect._rotowire_doc(pid)  # one player's feed; not _poll_rotowire, whose id list may build the pool
    _need(doc is not None, f"no feed for player {pid} (_rotowire_doc hides the HTTP error)")
    return f"player {pid}: {len(doc.get('feed') or [])} items"


def seed_text(source: dict) -> Callable[[], str]:
    def check() -> str:
        from ffl.newsdesk import collect
        items = collect.poll(source)  # fetch + parse only; _collect is the part that stores
        return f"{len(items)} items"
    return check


def seed_media(source: dict) -> Callable[[], str]:
    def check() -> str:
        from ffl.newsdesk import media
        got = media._feed(source)  # the RSS/podcast feed; returns the exception rather than raising
        if isinstance(got, Exception):
            raise got
        rows, _ = got
        if source["kind"] == "youtube":
            with _MEMO_LOCK:
                _MEMO.setdefault("videos", []).extend(r["video_id"] for r in sorted(rows, key=lambda r: r["published_at"],
                                                                                   reverse=True)[:2])
        return f"{len(rows)} entries"
    return check


def youtube_api() -> str:
    from ffl import appconf
    from ffl.newsdesk import media, seed
    key = appconf.get(appconf.YOUTUBE_KEY)
    _need(key, "no YouTube API key (Settings › App)")
    src = next(s for s in seed.SOURCES if s["kind"] == "youtube")
    rows = media._uploads(src, key)  # 1 quota unit; the key rides in a header
    return f"{src['id']}: {len(rows)} uploads"


def youtube_transcript() -> str:
    _off("youtube_captions")
    from ffl.newsdesk import media
    vids = list(dict.fromkeys(_MEMO.get("videos") or []))
    _need(vids, "no video ids (every yt feed failed)")
    tried = []
    for vid in vids[:3]:  # captions() spaces calls GAP_S (10 s) apart
        segs = media.captions(vid)  # None: no captions yet (not a refusal); raises when YouTube refuses
        if segs:
            return f"{vid}: {len(segs)} segments"
        tried.append(vid)
    raise RuntimeError(f"no captions on {tried} (none refused)")


def claude_p() -> str:
    from ffl import claude_cli
    out = claude_cli.run("Reply with exactly: ok", model="haiku",
                         schema={"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]},
                         tools="", cwd=claude_cli.neutral_cwd("source-check"), timeout=120)
    _need(str(out.get("reply", "")).strip().lower().startswith("ok"), f"unexpected reply {out!r}"[:200])
    return f"reply {out.get('reply')!r}"


def checks() -> list[Check]:
    """Built after the registry read (two leagues) and the ADP feed keys; the seed feeds last but the slow tail."""
    from ffl import adp, leagues, settings
    from ffl.newsdesk import seed

    out: list[Check] = []
    refs = leagues.all_leagues()
    for ref in refs:
        out += [(f"espn-{view}[{ref.key}]", espn_private(ref, view)) for view in ("mRoster", "mMatchup")]
    out += [("espn-injuries", espn_injuries), ("espn-pro-schedule", espn_pro_schedule),
            ("espn-players-index", espn_players_index), ("fastcast", fastcast),
            ("nfl-scoreboard", nfl_scoreboard), ("nfl-drives", nfl_drives),
            ("sleeper-week", sleeper_week), ("sleeper-stats", sleeper_stats), ("sleeper-season", sleeper_season),
            ("sleeper-players", sleeper_players), ("cbs-ros", cbs_ros), ("cbs-week", cbs_week),
            ("fantasysharks", sharks), ("fleaflicker", fleaflicker)]
    feeds: set[tuple[str, int, int]] = set()
    for ref in refs:
        try:
            feeds |= set(adp.feed_keys(settings.load(ref)).values())
        except Exception as e:  # noqa: BLE001
            out.append((f"ffc-adp[{ref.key}]", lambda e=e: (_ for _ in ()).throw(e)))
    out += [(f"ffc-adp[{f}/{t}/{y}]", ffc_adp(f, t, y)) for f, t, y in sorted(feeds)]
    for s in seed.SOURCES:
        if s.get("active") is False:
            continue  # registered inactive (abandoned feeds): the desk never polls them
        if s["kind"] == "rotowire":
            out.append(("seed-rotowire", rotowire))
        elif s["kind"] in ("youtube", "podcast"):
            out.append((f"seed-{s['id']}", seed_media(s)))
        else:
            out.append((f"seed-{s['id']}", seed_text(s)))
    return out


# run after the rest: they read what the feeds above left in _MEMO, or are slow
LATE: list[Check] = [("youtube-data-api", youtube_api), ("youtube-transcript", youtube_transcript),
                     ("claude-p", claude_p)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", nargs="*", help="run only checks whose name contains one of these")
    a = ap.parse_args(argv)
    try:
        first = checks()
    except BaseException as e:  # noqa: BLE001 — the registry read itself (DB) failed
        return report([("league-registry", "FAIL", _err(e))])
    # the ESPN reads first: rotowire takes a player id off mRoster
    espn = [c for c in first if c[0].startswith("espn-m")]
    rest = [c for c in first if not c[0].startswith("espn-m")]
    results = run(select(espn, a.only)) + run(select(rest, a.only)) + run(select(LATE, a.only), workers=1)
    return report(results)


if __name__ == "__main__":
    sys.exit(main())
