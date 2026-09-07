"""Environment/config loading and League factory."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / (os.environ.get("FFL_DATA_DIR") or "data")  # override for mock/practice runs
JOURNAL = ROOT / "journal"
DATA.mkdir(exist_ok=True)

load_dotenv(ROOT / ".env")

LEAGUE_ID = int(os.environ.get("LEAGUE_ID", "0") or 0)
ESPN_S2 = os.environ.get("ESPN_S2") or None
SWID = os.environ.get("SWID") or None
SEASON = int(os.environ.get("SEASON", "2026"))
MY_TEAM_ID = int(os.environ.get("MY_TEAM_ID", "0") or 0)  # set after settings dump identifies it
# Chrome remote-debugging port. Override when 9222 is already taken by another project's
# Chrome: every CDP consumer here would otherwise attach to that browser instead of ours.
CDP_PORT = int(os.environ.get("FFL_CDP_PORT") or 9222)

DRAFT_ROOM_URL = f"https://fantasy.espn.com/football/draft?leagueId={LEAGUE_ID}"
LEAGUE_URL = f"https://fantasy.espn.com/football/league?leagueId={LEAGUE_ID}"


def cookies() -> dict | None:
    if ESPN_S2 and SWID:
        return {"espn_s2": ESPN_S2, "SWID": SWID}
    return None


@lru_cache(maxsize=1)
def league(fetch_league: bool = True):
    from espn_api.football import League

    if not LEAGUE_ID:
        raise SystemExit("LEAGUE_ID missing: copy .env.example to .env and fill it in")
    return League(league_id=LEAGUE_ID, year=SEASON, espn_s2=ESPN_S2, swid=SWID, fetch_league=fetch_league)


def raw_league_get(view, extend: str = "", headers: dict | None = None, **params) -> dict:
    """Direct call to ESPN's league endpoint with the given view(s)."""
    lg = league()
    params = {"view": view, **params}
    return lg.espn_request.league_get(params=params, headers=headers, extend=extend)
