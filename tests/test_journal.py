"""journal.sync_results writes weekly results into the DB. No network here.

POST /refresh calls sync_results directly, so the results must land without journal.main.
"""
from types import SimpleNamespace

from ffl import db, journal


def _fake_team():
    return SimpleNamespace(team_id=7, team_name="Me", wins=1, losses=0,
                           points_for=100.0, points_against=90.0)


def test_sync_results_writes_rows(monkeypatch):
    me, opp = _fake_team(), SimpleNamespace(team_id=3, team_name="Them")
    m = SimpleNamespace(home_team=me, away_team=opp, home_score=110.0, away_score=90.0)
    lg = SimpleNamespace(current_week=2, teams=[me], standings=lambda: [me], scoreboard=lambda wk: [m])
    monkeypatch.setattr(journal, "MY_TEAM_ID", None)
    monkeypatch.setattr(journal, "SWID", "{ME}")
    journal.sync_results(lg, {"teams": [{"id": 7, "owner_swids": ["{ME}"]}]})
    assert db.results_rows() == [{"week": 1, "opponent": "Them", "my_score": 110.0, "opp_score": 90.0, "result": "W"}]


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


def test_a_failing_listener_never_fails_the_sync(monkeypatch):
    """The rows are logged before the listeners run: one that raises (the API's page refresh) is logged, and the
    sync still answers its new rows, so the tick and /transactions/sync do not report a failed sync."""
    topics = {"topics": [{"date": 1788937502756, "messages": [{"messageTypeId": 180, "to": 6, "targetId": 5083754, "from": 0}]}]}
    team = SimpleNamespace(team_id=6, team_name="Henry", roster=[])
    lg = SimpleNamespace(year=2026, player_map={5083754: "Ryan Flournoy", "Ryan Flournoy": 5083754},
                         get_team_data=lambda tid: team,
                         espn_request=SimpleNamespace(league_get=lambda extend="", params=None, headers=None: topics))

    def boom(rows):
        raise RuntimeError("listener down")
    monkeypatch.setattr(journal, "ON_NEW_ROWS", [boom])
    assert len(journal.sync_transactions(lg)) == 1
