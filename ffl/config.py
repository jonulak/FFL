"""Environment/config loading and League factory."""
from __future__ import annotations

import os
from datetime import date
from functools import lru_cache
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


def __getattr__(name: str):
    """TRANSITIONAL (phase 1b removes): the home league's facts for callers not yet converted to `ffl.leagues`.
    Lazy, because leagues -> db -> config: reading them at config import time would be circular."""
    if name not in ("LEAGUE_ID", "MY_TEAM_ID", "DRAFT_ROOM_URL", "LEAGUE_URL"):
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import leagues
    ref = leagues.home()
    if name == "LEAGUE_ID":
        return int(ref.provider_id)
    if name == "MY_TEAM_ID":
        return ref.team_id or 0
    page = "draft" if name == "DRAFT_ROOM_URL" else "league"
    return f"https://fantasy.espn.com/football/{page}?leagueId={ref.provider_id}"


def cookies() -> dict | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


@lru_cache(maxsize=1)
def league(fetch_league: bool = True):
    from espn_api.football import League

    return League(league_id=__getattr__("LEAGUE_ID"), year=SEASON, espn_s2=ESPN_S2, swid=SWID, fetch_league=fetch_league)


def raw_league_get(view, extend: str = "", headers: dict | None = None, **params) -> dict:
    """Direct call to ESPN's league endpoint with the given view(s)."""
    lg = league()
    params = {"view": view, **params}
    return lg.espn_request.league_get(params=params, headers=headers, extend=extend)
