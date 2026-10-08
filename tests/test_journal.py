"""journal.sync_results writes into journal/results/ even when that directory does not exist yet.

POST /refresh calls sync_results directly (not through journal.main, which used to be the only
thing making the directory), so a fresh checkout 500'd on FileNotFoundError. No network here.
"""
from types import SimpleNamespace

from ffl import db, journal


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


def test_sync_transactions_empty_feed_adds_nothing():
    """The UI's Refresh button syncs on every click; an empty feed leaves the log empty."""
    lg = SimpleNamespace(player_map={}, get_team_data=lambda tid: None,
                         espn_request=SimpleNamespace(league_get=lambda **kw: {"topics": []}))

    journal.sync_transactions(lg)
    journal.sync_transactions(lg)

    assert db.transactions() == []


def test_sync_transactions_resolves_dropped_players_locally():
    """A dropped player is on nobody's roster, and espn-api's recent_activity answers that with two
    ESPN requests per player: two dozen drops put POST /refresh past the UI's 20 s abort. The sync
    must name him from the league's player map, never the network, and keep the id-based row key."""
    team = SimpleNamespace(team_id=6, team_name="Henry", roster=[])
    topics = {"topics": [{"date": 1788937502756, "messages": [
        {"messageTypeId": 179, "to": 6, "targetId": 4688380, "from": 0},   # DROPPED
        {"messageTypeId": 180, "to": 6, "targetId": 5083754, "from": 0},   # WAIVER ADDED
    ]}]}

    def boom(**kw):
        raise AssertionError(f"player_info hit the network for {kw}")

    lg = SimpleNamespace(
        year=2026,
        player_map={4688380: "Cam Ward", "Cam Ward": 4688380, 5083754: "Ryan Flournoy"},
        get_team_data=lambda tid: team,
        player_info=boom,
        recent_activity=lambda size=100: boom(size=size),
        espn_request=SimpleNamespace(league_get=lambda extend="", params=None, headers=None: topics),
    )

    journal.sync_transactions(lg)

    rows = db.transactions()
    assert [(r["id"], r["player"]) for r in rows] == [("1788937502756-6-DROPPED-4688380", "Cam Ward"),
                                                      ("1788937502756-6-WAIVER ADDED-5083754", "Ryan Flournoy")]
