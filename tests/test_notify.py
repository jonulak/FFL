"""ffl/notify.py: detectors are pure over parsed inputs; the outbox is the DB's notices table."""
from types import SimpleNamespace

from ffl import db, journal, notify

AT = "2026-10-09T10:00:00-04:00"


def _tx(i, action, team="Bears", player="Isiah Pacheco"):
    return {"id": f"1788937502756-6-{action}-{i}", "date": "2026-10-09T03:15", "type": action, "team": team,
            "player": player, "player_id": i, "action": action, "bid": 0, "reasoning": ""}


def test_move_notices_one_per_row_with_sign():
    out = notify.move_notices([_tx(1, "WAIVER ADDED"), _tx(2, "DROPPED", player="Jaylen Wright")], AT)
    assert [n["key"] for n in out] == ["move:1788937502756-6-WAIVER ADDED-1", "move:1788937502756-6-DROPPED-2"]
    assert [n["body"] for n in out] == ["Bears +Isiah Pacheco", "Bears −Jaylen Wright"]
    assert {n["kind"] for n in out} == {"move"} and {n["url"] for n in out} == {"/transactions"}


def test_trade_rows_are_kind_trade_and_the_sent_side_is_skipped():
    out = notify.move_notices([_tx(1, "TRADED", team="Bears"), _tx(2, "TRADE_SENT", team="Lions")], AT)
    assert [(n["kind"], n["body"]) for n in out] == [("trade", "Bears get Isiah Pacheco")]


def test_sync_transactions_queues_only_new_rows():
    topics = {"topics": [{"date": 1788937502756, "messages": [{"messageTypeId": 180, "to": 6, "targetId": 5083754, "from": 0}]}]}
    team = SimpleNamespace(team_id=6, team_name="Henry", roster=[])
    lg = SimpleNamespace(year=2026, player_map={5083754: "Ryan Flournoy"}, get_team_data=lambda tid: team,
                         espn_request=SimpleNamespace(league_get=lambda extend="", params=None, headers=None: topics))
    first = journal.sync_transactions(lg)
    second = journal.sync_transactions(lg)
    assert len(first) == 1 and second == []
    assert [n["body"] for n in db.unsent_notices()] == ["Henry +Ryan Flournoy"]


def test_rows_already_logged_before_notify_never_fire():
    db.add_transactions([_tx(1, "WAIVER ADDED")])          # the log as it stood at deploy
    assert db.unsent_notices() == []
