"""Live overlay, the connect/idle decision, and the version state — no socket."""
import copy
import json
import threading
from datetime import datetime, timedelta, timezone
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
    assert live.overlay_league({"scoreboard": board}, {1: 10.0})["scoreboard"] == [{**board[0], "live": False, "final": False}]


def test_overlay_league_marks_only_matchups_with_a_starter_in_a_game_on():
    board = [{"home": {"team_id": 1, "score": 0.0, "starter_teams": ["KC"]},
              "away": {"team_id": 2, "score": 0.0, "starter_teams": ["BUF"]}},
             {"home": {"team_id": 3, "score": 0.0, "starter_teams": ["DAL"]},
              "away": {"team_id": 4, "score": 0.0, "starter_teams": ["NYG", "PHI"]}},
             {"home": {"team_id": 5, "score": 0.0, "starter_teams": []},
              "away": {"team_id": 6, "score": 0.0}}]
    games = {"BUF": {"status": "in"}, "MIA": {"status": "in"}, "DAL": {"status": "post"}, "NYG": {"status": "pre"}}
    out = live.overlay_league({"scoreboard": board}, {}, games)["scoreboard"]
    assert [m["live"] for m in out] == [True, False, False], "a game on elsewhere does not make a tile live"


def test_game_on_reads_both_sides_lineups_against_the_games_in_progress():
    me = {"lineup": [{"team": "KC"}, {"team": "DAL"}]}
    opp = {"lineup": [{"team": "BUF"}]}
    games = {"MIA": {"status": "in"}, "DAL": {"status": "post"}, "BUF": {"status": "in"}}
    assert live.game_on([me, opp], games) is True
    assert live.game_on([me, None], games) is False, "a game on elsewhere, or a final, is not this board's"
    assert live.game_on([me, opp], {}) is False


def test_idle_stays_connected_during_a_game():
    doc = copy.deepcopy(FIX)
    doc["events"][0]["status"] = "in"
    assert live.idle_for(doc, datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)) is None


def test_idle_stays_connected_just_before_kickoff():
    now = datetime(2026, 9, 13, 16, 50, tzinfo=timezone.utc)  # 1 PM ET slate at 17:00Z
    assert live.idle_for(FIX, now) is None


def test_idle_stays_connected_past_a_kickoff_that_has_not_happened_yet():
    # 8:15 PM is the scheduled time; the game reads "pre" until the ball is actually kicked,
    # and the next slate is days out. Sleeping here missed the first half hour of every TNF.
    now = datetime(2026, 9, 13, 17, 4, tzinfo=timezone.utc)
    assert live.idle_for(FIX, now) is None


def test_idle_gives_up_on_a_game_long_past_its_kickoff():
    # a postponed game keeps "pre" for days; it must not hold the socket open
    now = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc) + timedelta(seconds=live.LATE_KICKOFF)
    assert live.idle_for(FIX, now) == live.MAX_IDLE


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


def test_a_move_wakes_the_stream_without_touching_the_points_version():
    """A logged move ticks `moves`, never `version`: the runner reads version == 0 as "first checkpoint, a
    baseline", and a move before kickoff must not turn that checkpoint into a burst of plays."""
    st = live.State()
    got = []
    t = threading.Thread(target=lambda: got.append(st.wait_for_tick((0, 0), timeout=5)))
    t.start()
    st.moved()
    t.join(2)
    assert got == [(0, 1)] and st.version == 0
    assert st.update({1: 2.0}) and st.wait_for_tick((0, 1), timeout=0.05) == (1, 1)
    assert st.wait_for_tick((1, 1), timeout=0.05) == (1, 1), "timeout returns the current tick"


def test_the_stream_opens_with_both_counts():
    """One opening message (a live page skips exactly one), carrying the moves count, so a page that reconnects
    after the phone slept can tell a move happened while it was away."""
    assert live.stream_open((17, 3)) == "retry: 3000\ndata: 17 m3\n\n"


def test_stream_lines_name_a_move_apart_from_points():
    assert live.stream_lines((3, 0), (3, 1)) == ["data: m1\n\n"]
    assert live.stream_lines((3, 1), (4, 1)) == ["data: 4\n\n"]
    assert live.stream_lines((3, 1), (4, 2)) == ["data: m2\n\n", "data: 4\n\n"]
    assert live.stream_lines((3, 1), (3, 1)) == []


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


SCORING = [{"stat_id": 3, "label": "Passing Yards", "points": 0.04}, {"stat_id": 4, "label": "TD Pass", "points": 4.0},
           {"stat_id": 20, "label": "Interceptions Thrown", "points": -2.0},
           {"stat_id": 53, "label": "Each reception", "points": 1.0},
           {"stat_id": 92, "label": "14-17 points allowed", "points": 1.0}, {"stat_id": 99, "label": "Each Sack", "points": 1.0},
           {"stat_id": 198, "label": "FG Made (50-59 yards)", "points": 5.0}, {"stat_id": 80, "label": "FG Made (0-39 yards)", "points": 3.0},
           {"stat_id": 85, "label": "Total FG Missed", "points": -1.0}]


def test_box_score_is_count_times_rate_and_sums_to_the_points():
    from ffl import fastcast
    row = {"0": 30.0, "1": 20.0, "3": 250.0, "4": 2.0, "20": 1.0, "23": 3.0}
    items = live.box_score(row, SCORING)
    assert [(i["label"], i["count"], i["points"]) for i in items] == [
        ("Passing Yards", 250.0, 10.0), ("TD Pass", 2.0, 8.0), ("Interceptions Thrown", 1.0, -2.0)]
    table = {int(s["stat_id"]): s["points"] for s in SCORING}
    assert round(sum(i["points"] for i in items), 2) == round(fastcast.player_points(row, table), 2)


def test_box_score_band_rows_show_the_total_they_were_read_off():
    items = live.box_score({"120": 17.0, "92": 1.0, "99": 3.0}, SCORING)
    assert [(i["label"], i["count"], i["flat"], i["points"]) for i in items] == [
        ("14-17 points allowed", 17.0, True, 1.0), ("Sack", 3.0, False, 3.0)]


def test_box_score_sorts_long_field_goals_with_the_kicks():
    items = live.box_score({"80": 1.0, "198": 1.0, "85": 1.0}, SCORING)
    assert [i["stat_id"] for i in items] == [80, 198, 85]


def test_overlay_falls_back_to_espns_stat_row_and_drops_it():
    p = {**player(7), "pos": "WR", "stat_row": {"53": 4.0, "42": 61.0}}
    out = live.overlay_players([p], {}, scoring=SCORING)[0]
    assert "stat_row" not in out
    assert out["stat_line"] == "4 rec, 61 yds"
    assert out["box"] == [{"stat_id": 53, "label": "Reception", "count": 4.0, "rate": 1.0, "flat": False, "points": 4.0}]
    fed = live.overlay_players([p], {7: 9.0}, stats={7: {"53": 5.0}}, scoring=SCORING)[0]
    assert fed["box"][0]["count"] == 5.0, "the feed's row wins while it has him"
    assert "box" not in live.overlay_players([p], {})[0], "no scoring table, no breakdown"
    assert out["volume"] == {"count": 0, "label": "targets"}, "the stat row carries his volume too"
    assert fed["volume"] == {"count": 0, "label": "targets"}
    assert "volume" not in live.overlay_players([{**player(7), "pos": "WR"}], {})[0], "no row, no volume"


def test_volume_is_the_positions_usage_count():
    assert live.volume("WR", {"53": 4.0, "58": 9.0}) == {"count": 9, "label": "targets"}
    assert live.volume("TE", {"58": 1.0}) == {"count": 1, "label": "target"}
    assert live.volume("RB", {"23": 18.0}) == {"count": 18, "label": "carries"}
    assert live.volume("RB", {"23": 1.0}) == {"count": 1, "label": "carry"}
    assert live.volume("QB", {"0": 34.0, "1": 22.0}) == {"count": 34, "label": "pass attempts"}
    assert live.volume("QB", {}) == {"count": 0, "label": "pass attempts"}
    assert live.volume("K", {"83": 1.0}) is None and live.volume("D/ST", {}) is None


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


def test_runner_tags_the_points_with_the_week_of_the_roster_it_scored(monkeypatch):
    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    current = {"week": 1}
    r = live.Runner(state=live.State(), settings={"scoring": []},
                    week_fn=lambda: {"week": current["week"], "games": []})
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.week == 1
    current["week"] = 2
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.week == 2


def test_a_failed_plays_write_is_recorded_not_raised(monkeypatch):
    from ffl import db

    class Failing:
        def add(self, rows):
            raise db.DBError("database is locked")

        def record(self, *a, **k):
            pass

    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    r = live.Runner(state=live.State(), settings={"scoring": []}, week_fn=lambda: {"week": 1, "games": []})
    r._on_doc(copy.deepcopy(FIX))  # the baseline
    r.store = Failing()
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.error.startswith("plays not saved")


def test_field_marks_the_offense_with_the_ball_and_the_defense_without_it():
    rz = {"status": "in", "ball": "BUF", "red_zone": True}
    mid = {"status": "in", "ball": "NE", "red_zone": False}
    assert live.field("QB", "BUF", rz) == "red_zone" and live.field("K", "BUF", rz) == "red_zone"
    assert live.field("D/ST", "NE", rz) == "red_zone", "a D/ST defending its own 20"
    assert live.field("D/ST", "BUF", rz) is None and live.field("WR", "NE", rz) is None
    assert live.field("RB", "NE", mid) == "on" and live.field("D/ST", "BUF", mid) == "on"
    assert live.field("QB", "BUF", {"status": "in"}) is None, "no ball (halftime): nobody is marked"


def test_overlay_sets_field_only_while_a_unit_is_on():
    games = {"BUF": {"status": "in", "ball": "BUF", "red_zone": False}, "NE": {"status": "in", "ball": "BUF", "red_zone": False}}
    out = live.overlay_players([{"player_id": 1, "pos": "QB", "team": "BUF"}, {"player_id": 2, "pos": "D/ST", "team": "NE"},
                                {"player_id": 3, "pos": "WR", "team": "NE"}], {}, games)
    assert [p.get("field") for p in out] == ["on", "on", None]


def test_a_change_of_possession_bumps_the_version_but_the_clock_does_not():
    st = live.State()
    st.update({}, {"BUF": {"status": "in", "quarter": 3, "clock": "2:29", "ball": "NE", "red_zone": False}})
    v = st.version
    st.update({}, {"BUF": {"status": "in", "quarter": 3, "clock": "2:10", "ball": "NE", "red_zone": False}})
    assert st.version == v
    st.update({}, {"BUF": {"status": "in", "quarter": 3, "clock": "2:10", "ball": "NE", "red_zone": True}})
    assert st.version == v + 1


# --- win probability on the overlay ------------------------------------------------------------

def wp(pid, pos="WR", proj=10.0, actual=0.0, team="KC"):
    return {"player_id": pid, "name": f"p{pid}", "slot": pos, "pos": pos, "team": team, "proj": proj,
            "actual": actual, "game_played": 0, "bye": False, "injury": "ACTIVE"}


def test_overlay_week_moves_win_prob_with_live_points():
    payload = {"week": 5, "me": {"lineup": [wp(1)], "bench": [], "ir": [], "actual_total": 0.0},
               "opp": {"lineup": [wp(2, team="BUF")], "bench": [], "ir": [], "actual_total": 0.0},
               "win_prob": {"me": 0.5, "opp": 0.5, "sigma_source": "fallback"}}
    games = {"KC": {"status": "in", "quarter": 4, "clock": "1:00"}, "BUF": {"status": "in", "quarter": 4, "clock": "1:00"}}
    out = live.overlay_week(payload, {1: 25.0, 2: 3.0}, games)
    assert out["win_prob"]["me"] > 0.99 and out["win_prob"]["sigma_source"] == "fallback"
    assert abs(out["win_prob"]["me"] + out["win_prob"]["opp"] - 1) < 0.002
    assert payload["win_prob"]["me"] == 0.5, "input untouched"


def test_overlay_week_reads_the_sigma_it_is_given():
    payload = {"week": 5, "me": {"lineup": [wp(1, proj=14.0)], "bench": [], "ir": [], "actual_total": 0.0},
               "opp": {"lineup": [wp(2, proj=10.0, team="BUF")], "bench": [], "ir": [], "actual_total": 0.0},
               "win_prob": {"me": 0.5, "opp": 0.5, "sigma_source": "calibrated"}}
    tight = live.overlay_week(payload, {}, sigma={"WR": 1.0})["win_prob"]["me"]
    loose = live.overlay_week(payload, {}, sigma={"WR": 20.0})["win_prob"]["me"]
    assert tight > 0.99 > loose > 0.5


def test_overlay_week_leaves_a_solo_week_without_odds():
    payload = {"week": 5, "me": {"lineup": [wp(1)], "bench": [], "ir": [], "actual_total": 0.0}, "opp": None, "win_prob": None}
    assert live.overlay_week(payload, {1: 5.0})["win_prob"] is None


def _tile_side(pid, team):
    return {"team_id": pid, "score": 0.0, "proj": 10.0, "me": False, "starter_ids": [pid],
            "starter_teams": [team], "pending_teams": [team], "starters": [wp(pid, team=team)]}


def test_overlay_league_recomputes_from_starters():
    payload = {"week": 5, "scoreboard": [{"away": _tile_side(1, "KC"), "home": _tile_side(2, "BUF"), "live": False,
                                          "final": False, "win_prob": {"away": 0.5, "home": 0.5}}]}
    games = {"KC": {"status": "post"}, "BUF": {"status": "post"}}
    out = live.overlay_league(payload, {1: 12.0, 2: 9.0}, games)
    assert out["scoreboard"][0]["win_prob"] == {"away": 1.0, "home": 0.0}


def test_overlay_league_keeps_a_schedule_week_without_odds():
    empty = {"starter_ids": [], "starter_teams": [], "pending_teams": [], "starters": [], "score": 0, "proj": 0}
    row = {"away": dict(empty), "home": dict(empty), "live": False, "final": False, "win_prob": None}
    assert live.overlay_league({"week": 9, "scoreboard": [row]}, {})["scoreboard"][0]["win_prob"] is None


def test_a_failed_plays_read_leaves_the_week_unadvanced_and_retries(monkeypatch):
    from ffl import db, plays

    real, calls = db.plays_rows, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise db.DBError("database is locked")
        return real(*a, **k)

    monkeypatch.setattr(db, "plays_rows", flaky)
    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    r = live.Runner(state=live.State(), settings={"scoring": []}, week_fn=lambda: {"week": 1, "games": []})
    r._refresh_roster()
    assert r.store is None and r.week is None and r.state.error.startswith("plays store not loaded")
    r._refresh_roster()
    assert isinstance(r.store, plays.Store) and r.week == 1


def test_a_recovered_plays_write_clears_only_its_own_error(monkeypatch):
    from ffl import plays

    class Recording:
        def __init__(self):
            self.written = []

        def add(self, rows):
            self.written.extend(rows)

        def record(self, *a, **k):
            pass

    events = []
    monkeypatch.setattr(plays, "events_from", lambda *a, **k: list(events))
    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    r = live.Runner(state=live.State(), settings={"scoring": []}, week_fn=lambda: {"week": 1, "games": []})
    r._on_doc(copy.deepcopy(FIX))  # the baseline
    r.store = Recording()
    r.state.error = "plays not saved: database is locked"
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.error.startswith("plays not saved"), "add([]) wrote nothing, so nothing has recovered"
    events.append({"player_id": 1})
    r._on_doc(copy.deepcopy(FIX))
    assert r.store.written == [{"player_id": 1}] and r.state.error is None
    r.state.error = "roster refresh failed: boom"
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.error == "roster refresh failed: boom"


def test_a_stale_schema_at_runtime_is_recorded_not_raised(monkeypatch):
    from ffl import db, plays

    def stale(*a, **k):
        raise db.SchemaError("ffl.db is at schema 'x', code wants 'y': run: uv run alembic upgrade head")

    class Stale:
        def add(self, rows):
            stale()

        def record(self, *a, **k):
            pass

    monkeypatch.setattr(db, "plays_rows", stale)
    monkeypatch.setattr(plays, "events_from", lambda *a, **k: [{"player_id": 1}])
    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    r = live.Runner(state=live.State(), settings={"scoring": []}, week_fn=lambda: {"week": 1, "games": []})
    r._refresh_roster()
    assert r.store is None and r.week is None and r.state.error.startswith("plays store not loaded")
    r._on_doc(copy.deepcopy(FIX))  # the baseline
    r.store = Stale()  # the load keeps failing, so the store is only ever the one set here
    r._on_doc(copy.deepcopy(FIX))
    assert r.state.error.startswith("plays not saved")


def test_a_recovered_plays_load_clears_only_its_own_error(monkeypatch):
    from ffl import db

    real, calls = db.plays_rows, []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise db.DBError("database is locked")
        return real(*a, **k)

    monkeypatch.setattr(db, "plays_rows", flaky)
    monkeypatch.setattr(live, "ROSTER_REFRESH", 0)
    r = live.Runner(state=live.State(), settings={"scoring": []}, week_fn=lambda: {"week": 1, "games": []})
    r._refresh_roster()
    assert r.state.error.startswith("plays store not loaded")
    r._refresh_roster()
    assert r.week == 1 and r.state.error is None
    r.week, r.state.error = None, "roster refresh failed: boom"  # a new week loads; an unrelated error stays
    r._refresh_roster()
    assert r.week == 1 and r.state.error == "roster refresh failed: boom"
