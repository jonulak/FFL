"""journal.sync_results writes into journal/results/ even when that directory does not exist yet.

POST /refresh calls sync_results directly (not through journal.main, which used to be the only
thing making the directory), so a fresh checkout 500'd on FileNotFoundError. No network here.
"""
from types import SimpleNamespace

from ffl import journal


def _fake_team():
    return SimpleNamespace(team_id=7, team_name="Me", wins=1, losses=0,
                           points_for=100.0, points_against=90.0)


def test_sync_results_creates_results_dir(monkeypatch, tmp_path):
    team = _fake_team()
    lg = SimpleNamespace(current_week=1, teams=[team], standings=lambda: [team],
                         scoreboard=lambda wk: [])
    weekly = tmp_path / "results" / "weekly.csv"
    standings = tmp_path / "results" / "standings.md"
    assert not weekly.parent.exists()
    monkeypatch.setattr(journal, "WEEKLY_CSV", weekly)
    monkeypatch.setattr(journal, "STANDINGS_MD", standings)
    monkeypatch.setattr(journal, "MY_TEAM_ID", None)
    monkeypatch.setattr(journal, "SWID", "{ME}")
    settings = {"teams": [{"id": 7, "owner_swids": ["{ME}"]}]}

    journal.sync_results(lg, settings)

    assert weekly.exists() and standings.exists()
    assert weekly.read_text().startswith("week,opponent,my_score,opp_score,result")
    assert "Me (me)" in standings.read_text()
