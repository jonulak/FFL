"""The test database's home league: the one the template files under TEST_USER (team 7, their default)."""
from ffl import leagues

HOME_KEY = "espn:100000001"


def home_league() -> leagues.LeagueRef:
    """TEST_USER's view of the home league, as a signed-in request opens it."""
    return leagues.membership(0, HOME_KEY)


HOME_SWID = "{HOME-SWID}"


def connect_home() -> None:
    """TEST_USER's own ESPN cookies (ffl-soc.3), which every real write and pending read in the home league needs."""
    from ffl import espn_auth
    espn_auth.connect(0, "home-s2", HOME_SWID)
