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


IN_Q2 = {"status": "in", "quarter": 2, "clock": "9:09", "score": {"JAX": 14, "CLE": 3}}


def test_stat_line_is_the_scoring_stats_by_position():
    qb = {"0": 15.0, "1": 11.0, "3": 138.0, "4": 2.0, "20": 1.0, "23": 1.0, "24": 2.0}
    assert live.stat_line("QB", qb) == "11/15, 138 yds, 2 TD, 1 INT · 1 car, 2 yds"
    assert live.stat_line("QB", {"0": 15.0, "1": 11.0, "3": 138.0}) == "11/15, 138 yds"
    wr = {"53": 4.0, "58": 6.0, "42": 61.0, "43": 1.0, "72": 1.0}
    assert live.stat_line("WR", wr) == "4 rec, 61 yds, 1 TD, 1 FUM"
    rb = {"23": 12.0, "24": 57.0, "25": 1.0, "53": 2.0, "42": 13.0}
    assert live.stat_line("RB", rb) == "12 car, 57 yds, 1 TD · 2 rec, 13 yds"
    assert live.stat_line("RB", {"23": 12.0, "24": 57.0, "26": 1.0}) == "12 car, 57 yds, 1 2PT"
    k = {"83": 1.0, "84": 1.0, "86": 2.0, "87": 2.0, "77": 1.0}
    assert live.stat_line("K", k) == "FG 1/1, XP 2/2"
    assert live.stat_line("K", {"83": 1.0, "84": 2.0, "85": 1.0}) == "FG 1/2"
    dst = {"99": 2.0, "95": 1.0, "96": 0.0, "104": 1.0, "120": 14.0, "127": 250.0}
    assert live.stat_line("D/ST", dst) == "2 sk, 1 INT, 1 TD, 14 PA, 250 YA"
    assert live.stat_line("QB", {"210": 1.0}) is None, "nothing that scores: no line"
    assert live.stat_line("WR", {}) is None


def test_state_keeps_game_states_but_a_clock_tick_alone_does_not_bump_the_version():
    """Every GET reads the latest clock; the stream only wakes the page for something new."""
    st = live.State()
    assert st.update({1: 2.0}, {"JAX": IN_Q2}) and st.version == 1
    assert not st.update({1: 2.0}, {"JAX": {**IN_Q2, "clock": "9:02"}}) and st.version == 1
    assert st.games["JAX"]["clock"] == "9:02", "the clock is still stored"
    assert st.update({1: 2.0}, {"JAX": {**IN_Q2, "quarter": 3}}) and st.version == 2
    assert st.update({1: 2.0}, {"JAX": {"status": "post", "quarter": 4, "clock": "0:00"}}) and st.version == 3


def test_overlay_attaches_the_game_state_once_a_team_has_kicked_off():
    cards = [{**player(1), "team": "JAX"}, {**player(2), "team": "KC"}, {**player(3), "team": "SEA"}]
    games = {"JAX": IN_Q2, "KC": {"status": "pre", "quarter": 0, "clock": "0:00"},
             "SEA": {"status": "post", "quarter": 4, "clock": "0:00"}}
    out = live.overlay_roster({"cards": cards}, {}, games)["cards"]
    assert out[0]["game"] == IN_Q2
    assert "game" not in out[1], "before kickoff the kickoff time stands"
    assert out[2]["game"]["status"] == "post"
    week = {"week": 1, "me": {"lineup": [{**player(1), "team": "JAX"}], "bench": [], "ir": []}, "opp": None}
    assert live.overlay_week(week, {}, games)["me"]["lineup"][0]["game"] == IN_Q2
    assert "game" not in live.overlay_roster({"cards": cards}, {})["cards"][0], "no games: untouched"


def test_overlay_attaches_a_stat_line_for_players_with_scoring_stats():
    cards = [{**player(1), "team": "JAX", "pos": "QB"}, {**player(2), "team": "JAX", "pos": "K"}]
    stats = {1: {"0": 15.0, "1": 11.0, "3": 138.0}, 2: {"210": 1.0}}
    out = live.overlay_roster({"cards": cards}, {}, {}, stats)["cards"]
    assert out[0]["stat_line"] == "11/15, 138 yds"
    assert "stat_line" not in out[1], "a row with nothing that scores: no line"
    week = {"week": 1, "me": {"lineup": [{**player(1), "team": "JAX", "pos": "QB"}], "bench": [], "ir": []}, "opp": None}
    assert live.overlay_week(week, {}, {}, stats)["me"]["lineup"][0]["stat_line"] == "11/15, 138 yds"


def test_state_records_the_week_its_points_belong_to():
    st = live.State()
    st.update({1: 2.0}, week=1)
    assert st.week == 1 and st.snapshot()["week"] == 1
    st.update({1: 2.0}, week=2)
    assert st.week == 2


def test_runner_tags_the_points_with_the_week_of_the_roster_it_scored(monkeypatch, tmp_path):
    from ffl import plays
    monkeypatch.setattr(plays, "PLAYS_DIR", tmp_path)
    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    current = {"week": 1}
    r = live.Runner(state=live.State(), settings={"scoring": []},
                    week_fn=lambda: {"week": current["week"], "me": None, "opp": None})
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.week == 1
    current["week"] = 2
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.week == 2
