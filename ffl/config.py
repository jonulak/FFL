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


def _check_status(self, status: int, extend: str = "", params: dict | None = None, headers: dict | None = None):
    """espn-api's checkRequestStatus without its 401 retry, which pointed the request's LEAGUE_ENDPOINT, shared by
    every thread reading the cached League, at leagueHistory (an address for seasons before 2018) while it retried,
    and kept it there when the retry answered: a concurrent read got a 404, "League … does not exist" (#489)."""
    from espn_api.requests.espn_requests import ESPNAccessDenied
    if status == 401:
        if not self.cookies or "espn_s2" not in self.cookies or "SWID" not in self.cookies:
            raise ESPNAccessDenied("espn_s2 and swid are required")
        raise ESPNAccessDenied(f"League {self.league_id} cannot be accessed with the provided credentials")
    return _ESPN_CHECK(self, status, extend, params, headers)


from espn_api.requests.espn_requests import EspnFantasyRequests as _Req  # noqa: E402 — patched once, at import

_ESPN_CHECK = _Req.checkRequestStatus
_Req.checkRequestStatus = _check_status

_LEAGUES: dict[tuple[str, int, bool], object] = {}  # (provider_id, season, fetch_league) -> its espn-api League
_LEAGUES_BY: dict[tuple[str, int, bool], object] = {}  # the same key -> the espn_auth.Cookies that built it (None: none)
_LEAGUES_LOCK = threading.Lock()


def _build(ref, fetch_league: bool):
    """A League for `ref` under a member's working cookies (espn_auth.for_read), and whose they were. Cookies ESPN
    refuses are marked failing and the next member's tried; with nobody's left, a league that needs cookies raises
    espn_auth.NoConnection."""
    from espn_api.football import League
    from espn_api.requests.espn_requests import ESPNAccessDenied

    from . import espn_auth
    tried: set[int] = set()
    while True:
        c = espn_auth.for_read(ref, skip=frozenset(tried))
        try:
            built = League(league_id=int(ref.provider_id), year=ref.season, espn_s2=c.espn_s2 if c else None,
                           swid=c.swid if c else None, fetch_league=fetch_league)
        except ESPNAccessDenied as e:
            if c is None:
                raise espn_auth.NoConnection(ref.key) from e
            espn_auth.refused(c, str(e))
            tried.add(c.user_id)
            continue
        if c is not None:
            espn_auth.passed(c)
        return built, c


def league(ref, fetch_league: bool = True):
    """The espn-api League for `ref`, built once per (league, season, fetch_league).
    A League is a snapshot of one league's week, teams and settings, not a connection. The cache is a dict
    behind a lock, not an lru_cache, so one league's League can be dropped (`league.evict(ref)`) when its
    hour is up without the other leagues' being rebuilt; the build (an ESPN call) runs outside the lock,
    and two threads asking at once may both build, the first install standing. It carries one member's cookies
    (ffl-soc.3: shared reads only; `league.evict_user` drops every League built with a user's)."""
    key = (ref.provider_id, ref.season, fetch_league)
    with _LEAGUES_LOCK:
        hit = _LEAGUES.get(key)
    if hit is not None:
        return hit
    built, by = _build(ref, fetch_league)
    with _LEAGUES_LOCK:
        if key not in _LEAGUES:
            _LEAGUES_BY[key] = by
        return _LEAGUES.setdefault(key, built)


def _evict(ref) -> None:
    """Drop every cached League of `ref`, whichever fetch_league it was built with."""
    with _LEAGUES_LOCK:
        for key in [k for k in _LEAGUES if k[:2] == (ref.provider_id, ref.season)]:
            del _LEAGUES[key]
            _LEAGUES_BY.pop(key, None)


def _evict_user(user_id: int) -> None:
    """Drop every cached League built with `user_id`'s cookies (they changed, were refused, or the user left)."""
    with _LEAGUES_LOCK:
        for key in [k for k, by in _LEAGUES_BY.items() if by is not None and by.user_id == user_id]:
            _LEAGUES.pop(key, None)
            del _LEAGUES_BY[key]


def _cache_clear() -> None:
    """Drop every cached League, of every league."""
    with _LEAGUES_LOCK:
        _LEAGUES.clear()
        _LEAGUES_BY.clear()


league.evict = _evict
league.evict_user = _evict_user
league.cache_clear = _cache_clear


def raw_league_get(view, extend: str = "", headers: dict | None = None, *, ref, cookies=None, **params) -> dict:
    """Direct call to ESPN's league endpoint with the given view(s), for `ref`: under the cached League's cookies (a
    shared read), or under `cookies` (an espn_auth.Cookies: a read of what ESPN shows one team alone)."""
    params = {"view": view, **params}
    if cookies is None:
        from espn_api.requests.espn_requests import ESPNAccessDenied

        from . import espn_auth
        try:
            return league(ref).espn_request.league_get(params=params, headers=headers, extend=extend)
        except ESPNAccessDenied:
            # the cookies the League was built with are refused now: marked, and the read made once more under the
            # next member's (the rebuild); with nobody's left, the build raises espn_auth.NoConnection
            with _LEAGUES_LOCK:
                by = _LEAGUES_BY.get((ref.provider_id, ref.season, True))
            if by is None:
                raise
            espn_auth.refused(by, "ESPN refused a shared read")
            return league(ref).espn_request.league_get(params=params, headers=headers, extend=extend)
    from espn_api.requests.espn_requests import EspnFantasyRequests
    req = EspnFantasyRequests("nfl", ref.season, int(ref.provider_id), cookies=cookies.jar())
    return req.league_get(params=params, headers=headers, extend=extend)
