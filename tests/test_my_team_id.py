"""My team in a league is the membership's (ffl-soc.6.2.3): the ESPN keys in the environment decide nothing.

In the 2026-09-07 mock against humans we were team 9 while the registry said 7; the SWID used to correct that, and
would now hand a second user the cookie owner's team instead. A mock's membership names its team
(`ffl.leagues join <key> --user <email> --team <id>`).
"""
from ffl import espn_auth
from dataclasses import replace

import pytest

from ffl import config, leagues
from ffl import draft_watch as dw
from ffl.leagues import LeagueRef
from tests.home import home_league

SETTINGS = {"teams": [
    {"id": 7, "name": "Team 7", "owner_swids": ["{SOMEONE-ELSE}"]},
    {"id": 9, "name": "Jonathan's Scary Team", "owner_swids": ["{ME}"]},
]}


def _home_team(team_id):
    leagues.register(replace(home_league(), team_id=team_id), user_id=0)


def test_the_membership_names_the_team_whatever_the_swid_says(monkeypatch, capsys):
    """ffl-soc.6.2.3: the env's ESPN keys decide nothing. The SWID owning team 9 does not move a member on team 7 (it
    is another user's cookie as often as not); a wrong team is fixed with `ffl.leagues join --team`."""
    espn_auth.connect(0, "s2", "{ME}")
    _home_team(7)
    assert dw.my_team_id(SETTINGS, ref=home_league()) == 7
    assert "!!" not in capsys.readouterr().out


def test_a_second_member_keeps_their_own_team(monkeypatch):
    from ffl import db
    espn_auth.connect(0, "s2", "{ME}")      # the env cookie is team 9's
    _home_team(7)
    uid = db.create_user("b@example.com", None)
    leagues.join(home_league().key, "b@example.com", team_id=3)
    assert leagues.my_team_id(SETTINGS, leagues.membership(uid, home_league().key)) == 3


def test_gives_up_loudly_with_no_team_on_the_membership(monkeypatch):
    espn_auth.connect(0, "s2", "{ME}")      # a SWID that owns a team here fills nothing in
    _home_team(None)
    with pytest.raises(SystemExit, match="--team"):
        dw.my_team_id(SETTINGS, ref=home_league())


def test_other_league_never_uses_home_team_id(monkeypatch):
    espn_auth.connect(0, "s2", "{NOT-IN-THIS-LEAGUE}")
    _home_team(7)
    other = LeagueRef("espn:999", "espn", "999", 2026, "other", None, None, None)
    with pytest.raises(SystemExit, match="espn:999"):
        leagues.my_team_id(SETTINGS, other)


def test_the_ref_names_the_league_whose_team_it_is(monkeypatch):
    espn_auth.disconnect(0)
    _home_team(7)
    other = leagues.register(LeagueRef("espn:999", "espn", "999", 2026, "other", 4, None, None), user_id=0)
    assert leagues.my_team_id({**SETTINGS, "league_id": 100000001}, ref=other) == 4, "the ref, never the settings"
    assert leagues.my_team_id(SETTINGS, ref=home_league()) == 7
    with pytest.raises(SystemExit, match="espn:555"):
        leagues.my_team_id(SETTINGS, ref=LeagueRef("espn:555", "espn", "555", 2026, "x", None, None, None))


def test_espn_season_my_team_id_fails_with_runtime_error(monkeypatch):
    """The API's `except Exception` paths catch this; a SystemExit would escape them."""
    from ffl import espn_season
    espn_auth.disconnect(0)
    _home_team(None)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: SETTINGS)
    with pytest.raises(RuntimeError, match="Could not identify my team"):
        espn_season.my_team_id(None, ref=home_league())
