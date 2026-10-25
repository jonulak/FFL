"""Environment/config loading and League factory."""
from __future__ import annotations

import os
import threading
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / (os.environ.get("FFL_DATA_DIR") or "data")  # override for mock/practice runs
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


_HOME_ATTRS = ("LEAGUE_ID", "MY_TEAM_ID", "DRAFT_ROOM_URL", "LEAGUE_URL")


def __getattr__(name: str):
    """TRANSITIONAL (phase 1b removes): the home league's facts for callers not yet converted to `ffl.leagues`.
    Lazy, because leagues -> db -> config: reading them at config import time would be circular.
    Any other name is an ordinary missing attribute, so hasattr/getattr-with-default behave."""
    if name not in _HOME_ATTRS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import leagues
    ref = leagues.home()
    if name == "LEAGUE_ID":
        return int(ref.provider_id)
    if name == "MY_TEAM_ID":
        return ref.team_id or 0
    return draft_room_url(ref) if name == "DRAFT_ROOM_URL" else league_url(ref)


def cookies() -> dict | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


_LEAGUES: dict[tuple[str, int, bool], object] = {}  # (provider_id, season, fetch_league) -> its espn-api League
_LEAGUES_LOCK = threading.Lock()


def league(ref=None, fetch_league: bool = True):
    """The espn-api League for `ref` (None: the home league), built once per (league, season, fetch_league).
    A League is a snapshot of one league's week, teams and settings, not a connection. The cache is a dict
    behind a lock, not an lru_cache, so one league's League can be dropped (`league.evict(ref)`) when its
    hour is up without the other leagues' being rebuilt; the build (an ESPN call) runs outside the lock,
    and two threads asking at once may both build, the first install standing."""
    if ref is None:
        from . import leagues
        ref = leagues.home()
    key = (ref.provider_id, ref.season, fetch_league)
    with _LEAGUES_LOCK:
        hit = _LEAGUES.get(key)
    if hit is not None:
        return hit
    from espn_api.football import League

    built = League(league_id=int(ref.provider_id), year=ref.season, espn_s2=ESPN_S2, swid=SWID, fetch_league=fetch_league)
    with _LEAGUES_LOCK:
        return _LEAGUES.setdefault(key, built)


def _evict(ref=None) -> None:
    """Drop every cached League of `ref` (None: the home league), whichever fetch_league it was built with."""
    if ref is None:
        from . import leagues
        ref = leagues.home()
    with _LEAGUES_LOCK:
        for key in [k for k in _LEAGUES if k[:2] == (ref.provider_id, ref.season)]:
            del _LEAGUES[key]


def _cache_clear() -> None:
    """Drop every cached League, of every league."""
    with _LEAGUES_LOCK:
        _LEAGUES.clear()


league.evict = _evict
league.cache_clear = _cache_clear


def raw_league_get(view, extend: str = "", headers: dict | None = None, *, ref=None, **params) -> dict:
    """Direct call to ESPN's league endpoint with the given view(s), for `ref` (None: the home league)."""
    lg = league(ref)
    params = {"view": view, **params}
    return lg.espn_request.league_get(params=params, headers=headers, extend=extend)
