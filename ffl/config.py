"""Environment/config loading and League factory."""
from __future__ import annotations

import logging
import os
import threading
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / (os.environ.get("FFL_DATA_DIR") or "data")  # the data directory only (browser profile, caches); the database is FFL_DB_URL's
JOURNAL = ROOT / "journal"
DATA.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

ESPN_S2 = os.environ.get("ESPN_S2") or None
SWID = os.environ.get("SWID") or None
# Chrome remote-debugging port. Override when 9222 is already taken by another project's
# Chrome: every CDP consumer here would otherwise attach to that browser instead of ours.
CDP_PORT = int(os.environ.get("FFL_CDP_PORT") or 9222)


def nfl_season(d: date) -> int:
    """The NFL season a date falls in: it runs from the new league year (March) through the next winter's playoffs."""
    return d.year if d.month >= 3 else d.year - 1


SEASON = nfl_season(date.today())


def _espn_page(ref, page: str) -> str:
    return f"https://fantasy.espn.com/football/{page}?leagueId={ref.provider_id}"


def draft_room_url(ref) -> str:
    return _espn_page(ref, "draft")


def league_url(ref) -> str:
    return _espn_page(ref, "league")


SOURCES_KNOWN = frozenset({"sharks", "youtube_captions"})  # the names FFL_SOURCES_OFF can switch off
_WARNED: set[str] = set()


def sources_off() -> frozenset[str]:
    """FFL_SOURCES_OFF (a comma list, read at each call) as the known names it holds: outside sources this host does
    not ask at all, absent rather than failing. The box sets `sharks,youtube_captions` (both refuse AWS addresses;
    ffl-soc.6.1.14/.15). An unknown name (a typo, or a source with no switch such as `sleeper`) is ignored, with one
    warning per process."""
    names = {s.strip() for s in (os.environ.get("FFL_SOURCES_OFF") or "").split(",") if s.strip()}
    if (unknown := sorted(names - SOURCES_KNOWN - _WARNED)):
        _WARNED.update(unknown)
        logging.getLogger(__name__).warning("FFL_SOURCES_OFF: ignoring unknown %s (known: %s)",
                                            ", ".join(unknown), ", ".join(sorted(SOURCES_KNOWN)))
    return frozenset(names & SOURCES_KNOWN)


def source_off(name: str) -> bool:
    return name in sources_off()


def cookies() -> dict | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


_LEAGUES: dict[tuple[str, int, bool], object] = {}  # (provider_id, season, fetch_league) -> its espn-api League
_LEAGUES_LOCK = threading.Lock()


def league(ref, fetch_league: bool = True):
    """The espn-api League for `ref`, built once per (league, season, fetch_league).
    A League is a snapshot of one league's week, teams and settings, not a connection. The cache is a dict
    behind a lock, not an lru_cache, so one league's League can be dropped (`league.evict(ref)`) when its
    hour is up without the other leagues' being rebuilt; the build (an ESPN call) runs outside the lock,
    and two threads asking at once may both build, the first install standing."""
    key = (ref.provider_id, ref.season, fetch_league)
    with _LEAGUES_LOCK:
        hit = _LEAGUES.get(key)
    if hit is not None:
        return hit
    from espn_api.football import League

    built = League(league_id=int(ref.provider_id), year=ref.season, espn_s2=ESPN_S2, swid=SWID, fetch_league=fetch_league)
    with _LEAGUES_LOCK:
        return _LEAGUES.setdefault(key, built)


def _evict(ref) -> None:
    """Drop every cached League of `ref`, whichever fetch_league it was built with."""
    with _LEAGUES_LOCK:
        for key in [k for k in _LEAGUES if k[:2] == (ref.provider_id, ref.season)]:
            del _LEAGUES[key]


def _cache_clear() -> None:
    """Drop every cached League, of every league."""
    with _LEAGUES_LOCK:
        _LEAGUES.clear()


league.evict = _evict
league.cache_clear = _cache_clear


def raw_league_get(view, extend: str = "", headers: dict | None = None, *, ref, **params) -> dict:
    """Direct call to ESPN's league endpoint with the given view(s), for `ref`."""
    lg = league(ref)
    params = {"view": view, **params}
    return lg.espn_request.league_get(params=params, headers=headers, extend=extend)
