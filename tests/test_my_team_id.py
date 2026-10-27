"""The registry's team id is per-league; the SWID is per-account. They disagree in a mock.

In the 2026-09-07 mock against humans we were team 9, while the home league's registered team
(correct for our real league) said 7. Trusting the registry there would have tracked a stranger's
roster, computed needs from it, and predicted the wrong turns for the whole draft.
"""
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


def test_swid_wins_when_it_disagrees_with_the_registry(monkeypatch, capsys):
    monkeypatch.setattr(config, "SWID", "{ME}")
    _home_team(7)
    assert dw.my_team_id(SETTINGS, ref=home_league()) == 9
    assert "using 9" in capsys.readouterr().out


def test_no_warning_when_they_agree(monkeypatch, capsys):
    monkeypatch.setattr(config, "SWID", "{ME}")
    _home_team(9)
    assert dw.my_team_id(SETTINGS, ref=home_league()) == 9
    assert "!!" not in capsys.readouterr().out


def test_registry_is_the_fallback_when_the_swid_owns_nothing_here(monkeypatch):
    """A practice draft keeps our real team id but the room may not carry the ownership."""
    monkeypatch.setattr(config, "SWID", "{NOT-IN-THIS-LEAGUE}")
    _home_team(7)
    assert dw.my_team_id(SETTINGS, ref=home_league()) == 7


def test_gives_up_loudly_with_neither(monkeypatch):
    monkeypatch.setattr(config, "SWID", None)
    _home_team(None)
    with pytest.raises(SystemExit):
        dw.my_team_id(SETTINGS, ref=home_league())


def test_other_league_never_uses_home_team_id(monkeypatch):
    monkeypatch.setattr(config, "SWID", "{NOT-IN-THIS-LEAGUE}")
    _home_team(7)
    other = LeagueRef("espn:999", "espn", "999", 2026, "other", None, None, None)
    with pytest.raises(SystemExit, match="espn:999"):
        leagues.my_team_id(SETTINGS, other)


def test_the_ref_names_the_league_whose_team_it_is(monkeypatch):
    monkeypatch.setattr(config, "SWID", None)
    _home_team(7)
    other = leagues.register(LeagueRef("espn:999", "espn", "999", 2026, "other", 4, None, None), user_id=0)
    assert leagues.my_team_id({**SETTINGS, "league_id": 100000001}, ref=other) == 4, "the ref, never the settings"
    assert leagues.my_team_id(SETTINGS, ref=home_league()) == 7
    with pytest.raises(SystemExit, match="espn:555"):
        leagues.my_team_id(SETTINGS, ref=LeagueRef("espn:555", "espn", "555", 2026, "x", None, None, None))


def test_espn_season_my_team_id_fails_with_runtime_error(monkeypatch):
    """The API's `except Exception` paths catch this; a SystemExit would escape them."""
    from ffl import espn_season
    monkeypatch.setattr(config, "SWID", None)
    _home_team(None)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: SETTINGS)
    with pytest.raises(RuntimeError, match="Could not identify my team"):
        espn_season.my_team_id(None, ref=home_league())
