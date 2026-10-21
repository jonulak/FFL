"""ffl/notify.py: detectors are pure over parsed inputs; the outbox is the DB's notices table."""
from types import SimpleNamespace

import pytest

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


def _flag(slot, injury, locked=False, name="Isiah Pacheco"):
    return {"slot": slot, "injury": injury, "locked": locked, "name": name, "droppable": True, "order": 0}


def test_unlocked_starter_out_is_a_notice():
    out = notify.injury_notices({9: _flag("RB/WR/TE", "OUT")}, 5, AT)
    assert out == [{"key": "injury:5:9:OUT", "kind": "injury", "title": "Isiah Pacheco OUT",
                    "body": "Starting at FLEX. Tap to make the call.", "url": "/roster", "created_at": AT}]


def test_doubtful_and_ir_count_questionable_does_not():
    flags = {1: _flag("RB", "DOUBTFUL"), 2: _flag("WR", "INJURY_RESERVE"), 3: _flag("WR", "QUESTIONABLE"),
             4: _flag("QB", "ACTIVE")}
    assert [n["key"] for n in notify.injury_notices(flags, 5, AT)] == ["injury:5:1:DOUBTFUL", "injury:5:2:INJURY_RESERVE"]


def test_locked_bench_ir_and_unslotted_players_are_skipped():
    flags = {1: _flag("RB", "OUT", locked=True), 2: _flag("BE", "OUT"), 3: _flag("IR", "OUT"),
             4: {"injury": "OUT", "locked": False, "name": "X"}}
    assert notify.injury_notices(flags, 5, AT) == []


def test_same_status_twice_is_one_notice_a_new_status_another():
    db.add_notices(notify.injury_notices({9: _flag("RB", "DOUBTFUL")}, 5, AT))
    db.add_notices(notify.injury_notices({9: _flag("RB", "DOUBTFUL")}, 5, AT))
    db.add_notices(notify.injury_notices({9: _flag("RB", "OUT")}, 5, AT))
    assert [n["key"] for n in db.unsent_notices()] == ["injury:5:9:DOUBTFUL", "injury:5:9:OUT"]


PENDING = [{"id": "a", "add": 11, "drop": 21, "priority": 1}, {"id": "b", "add": 12, "drop": None, "priority": 2}]
NAMES = {11: "Ray Davis", 12: "Tyler Allgeier"}


def test_waiver_run_reports_won_and_lost():
    out = notify.waiver_notices("2026-10-08T03:12:00", "2026-10-01T03:10:00", PENDING, {11}, NAMES, AT)
    assert out == [{"key": "waivers:2026-10-08T03:12:00", "kind": "waivers", "title": "Waivers: 1 of 2 players won",
                    "body": "Won Ray Davis. Lost Tyler Allgeier.", "url": "/transactions", "created_at": AT}]


def test_waiver_first_sight_or_same_run_or_no_claims_is_silent():
    assert notify.waiver_notices("2026-10-08T03:12:00", None, PENDING, set(), NAMES, AT) == []
    assert notify.waiver_notices("2026-10-08T03:12:00", "2026-10-08T03:12:00", PENDING, set(), NAMES, AT) == []
    assert notify.waiver_notices(None, "2026-10-08T03:12:00", PENDING, set(), NAMES, AT) == []
    assert notify.waiver_notices("2026-10-08T03:12:00", "2026-10-01T03:10:00", [], set(), NAMES, AT) == []


def test_waiver_claim_still_pending_is_neither_won_nor_lost():
    run = ("2026-10-08T03:12:00", "2026-10-01T03:10:00")
    out = notify.waiver_notices(*run, PENDING, {11}, NAMES, AT, still_pending={"b"})
    assert out[0]["title"] == "Waivers: 1 of 1 players won" and out[0]["body"] == "Won Ray Davis."
    assert notify.waiver_notices(*run, PENDING, set(), NAMES, AT, still_pending={"a", "b"}) == []


def test_waiver_player_claimed_with_several_drops_is_named_and_counted_once():
    pending = [{"id": "a", "add": 11, "drop": 21, "priority": 1}, {"id": "b", "add": 11, "drop": 22, "priority": 2},
               {"id": "c", "add": 12, "drop": 21, "priority": 3}, {"id": "d", "add": 12, "drop": 22, "priority": 4}]
    out = notify.waiver_notices("2026-10-08T03:12:00", "2026-10-01T03:10:00", pending, {11}, NAMES, AT)
    assert out[0]["title"] == "Waivers: 1 of 2 players won" and out[0]["body"] == "Won Ray Davis. Lost Tyler Allgeier."


def test_waiver_unknown_name_falls_back_to_the_id():
    out = notify.waiver_notices("r2", "r1", [{"id": "a", "add": 99, "drop": None, "priority": 1}], set(), {}, AT)
    assert out[0]["body"] == "Lost player 99." and out[0]["title"] == "Waivers: 0 of 1 players won"


def test_source_notices_after_a_day_failing():
    from datetime import datetime, timedelta
    t = datetime(2026, 10, 12, 9, 0).astimezone()
    status = {"sharks": {"error": "thin scrape at RB", "failing_since": (t - timedelta(hours=25)).isoformat()},
              "cbs": {"error": "down", "failing_since": (t - timedelta(hours=2)).isoformat()},
              "fleaflicker": {"error": None, "failing_since": None}}
    out = notify.source_notices(status, t, "now")
    assert [n["key"] for n in out] == [f"source:sharks:{status['sharks']['failing_since']}"]
    assert out[0]["kind"] == "source" and "sharks" in out[0]["title"] and out[0]["url"] == "/players"


def test_put_prefs_accepts_news_bool():
    assert notify.prefs()["news"] is True
    assert notify.put_prefs({"news": False})["news"] is False and notify.prefs()["news"] is False
    with pytest.raises(ValueError):
        notify.put_prefs({"news": "yes"})
    assert notify.prefs()["news"] is False
