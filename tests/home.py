"""The test database's home league: the one the template files under TEST_USER (team 7, their default)."""
from ffl import leagues

HOME_KEY = "espn:100000001"


def home_league() -> leagues.LeagueRef:
    """TEST_USER's view of the home league, as a signed-in request opens it."""
    return leagues.membership(0, HOME_KEY)
