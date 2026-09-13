"""Live overlay, the connect/idle decision, and the version state — no socket."""
import copy
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from ffl import live

FIX = json.loads((Path(__file__).parent / "fixtures" / "fastcast_checkpoint.json").read_text())


def player(pid, actual=0.0, slot="QB"):
    return {"player_id": pid, "name": f"p{pid}", "slot": slot, "actual": actual, "proj": 10.0}


def test_overlay_week_replaces_actuals_and_resums_the_lineup():
    payload = {"week": 1, "me": {"lineup": [player(1, 2.0), player(2, 0.0)], "bench": [player(3)], "ir": [],
                                 "actual_total": 2.0},
               "opp": None}
    out = live.overlay_week(payload, {1: 12.5, 3: 4.0})
    assert [p["actual"] for p in out["me"]["lineup"]] == [12.5, 0.0]
    assert out["me"]["bench"][0]["actual"] == 4.0
    assert out["me"]["actual_total"] == 12.5
    assert payload["me"]["lineup"][0]["actual"] == 2.0, "input untouched"
    assert out["opp"] is None


def test_overlay_roster_touches_only_known_players():
    out = live.overlay_roster({"cards": [player(1, 1.0), player(2, 1.0)]}, {2: 9.9})
    assert [c["actual"] for c in out["cards"]] == [1.0, 9.9]


def test_overlay_league_resums_a_side_from_its_starters():
    board = [{"home": {"team_id": 1, "score": 0.0, "starter_ids": [1, 2]},
              "away": {"team_id": 2, "score": 3.0, "starter_ids": [3]}}]
    out = live.overlay_league({"scoreboard": board}, {1: 10.0, 2: 5.5})
    assert out["scoreboard"][0]["home"]["score"] == 15.5
    assert out["scoreboard"][0]["away"]["score"] == 3.0, "no live player: ESPN's number stands"


def test_overlay_league_without_starter_ids_is_a_no_op():
    board = [{"home": {"team_id": 1, "score": 7.0}, "away": {"team_id": 2, "score": 3.0}}]
    assert live.overlay_league({"scoreboard": board}, {1: 10.0})["scoreboard"] == board


def test_idle_stays_connected_during_a_game():
    doc = copy.deepcopy(FIX)
    doc["events"][0]["status"] = "in"
    assert live.idle_for(doc, datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)) is None


def test_idle_stays_connected_just_before_kickoff():
    now = datetime(2026, 9, 13, 16, 50, tzinfo=timezone.utc)  # 1 PM ET slate at 17:00Z
    assert live.idle_for(FIX, now) is None


def test_idle_sleeps_until_shortly_before_the_next_kickoff():
    now = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)  # 30 min out
    assert live.idle_for(FIX, now) == 30 * 60 - live.IDLE_BEFORE_KICKOFF


def test_idle_is_capped_and_defaults_when_nothing_is_left():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)  # Saturday, next game 29 h out
    assert live.idle_for(FIX, now) == live.MAX_IDLE
    done = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    assert live.idle_for(FIX, done) == live.MAX_IDLE


def test_state_version_ticks_only_on_change_and_wakes_waiters():
    st = live.State()
    assert st.update({1: 2.0}) and st.version == 1
    assert not st.update({1: 2.0}) and st.version == 1
    got = []
    t = threading.Thread(target=lambda: got.append(st.wait_for_change(1, timeout=5)))
    t.start()
    assert st.update({1: 3.0})
    t.join(2)
    assert got == [2]
    assert st.wait_for_change(2, timeout=0.05) == 2, "timeout returns the current version"


def test_state_first_update_always_ticks_even_when_empty():
    st = live.State()
    assert st.update({}) and st.version == 1
