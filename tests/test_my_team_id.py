"""MY_TEAM_ID is per-league; the SWID is per-account. They disagree in a mock.

In the 2026-09-07 mock against humans we were team 9, while `.env` (correct for our real
league) said 7. Trusting the env var there would have tracked a stranger's roster, computed
needs from it, and predicted the wrong turns for the whole draft.
"""
import pytest

from ffl import draft_watch as dw

SETTINGS = {"teams": [
    {"id": 7, "name": "Team 7", "owner_swids": ["{SOMEONE-ELSE}"]},
    {"id": 9, "name": "Jonathan's Scary Team", "owner_swids": ["{ME}"]},
]}


def test_swid_wins_when_it_disagrees_with_the_env_var(monkeypatch, capsys):
    monkeypatch.setattr(dw, "SWID", "{ME}")
    monkeypatch.setattr(dw, "MY_TEAM_ID", 7)
    assert dw.my_team_id(SETTINGS) == 9
    assert "using 9" in capsys.readouterr().out


def test_no_warning_when_they_agree(monkeypatch, capsys):
    monkeypatch.setattr(dw, "SWID", "{ME}")
    monkeypatch.setattr(dw, "MY_TEAM_ID", 9)
    assert dw.my_team_id(SETTINGS) == 9
    assert "!!" not in capsys.readouterr().out


def test_env_var_is_the_fallback_when_the_swid_owns_nothing_here(monkeypatch):
    """A practice draft keeps our real team id but the room may not carry the ownership."""
    monkeypatch.setattr(dw, "SWID", "{NOT-IN-THIS-LEAGUE}")
    monkeypatch.setattr(dw, "MY_TEAM_ID", 7)
    assert dw.my_team_id(SETTINGS) == 7


def test_gives_up_loudly_with_neither(monkeypatch):
    monkeypatch.setattr(dw, "SWID", None)
    monkeypatch.setattr(dw, "MY_TEAM_ID", 0)
    with pytest.raises(SystemExit):
        dw.my_team_id(SETTINGS)
