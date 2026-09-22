"""ffl.pace: the league-wide "who is beating his projection" table. The pure parts: opponent ranks off
the scoreboard, the row, the ranking filter and the rendered table."""
from ffl import pace


def _game(week, home, hs, away, as_, state="post"):
    return {"competitions": [{"status": {"type": {"state": state}},
                              "competitors": [{"homeAway": "home", "team": {"abbreviation": home}, "score": str(hs)},
                                              {"homeAway": "away", "team": {"abbreviation": away}, "score": str(as_)}]}]}


def test_parse_scoreboard_ranks_defences_by_points_allowed_and_keeps_opponents():
    docs = {1: {"events": [_game(1, "MIN", 39, "GB", 22), _game(1, "LV", 27, "MIA", 13)]},
            2: {"events": [_game(2, "CHI", 3, "MIN", 9), _game(2, "LV", 26, "LAC", 14), _game(2, "NE", 0, "NYJ", 0, state="in")]}}
    rank, sched = pace.parse_scoreboard(docs)
    # MIN allowed 22 and 3 (12.5), LV 13 and 14 (13.5), MIA 27, LAC 26, GB 39, CHI 9 (9.0 -> stingiest)
    assert rank["CHI"] == 1 and rank["MIN"] == 2 and rank["LV"] == 3 and rank["GB"] == len(rank)
    assert sched["MIN"] == {1: "GB", 2: "CHI"} and sched["GB"] == {1: "MIN"}
    assert "NE" not in rank, "a game still in progress is not a result"


def _player(name, pos, l3, pace_gap, games=2, ros=150.0, team="MIN"):
    return {"name": name, "pos": pos, "team": team, "injury": "ACTIVE", "proj": 10.0, "ros_proj": ros,
            "ros_sleeper": ros, "ros_cbs": ros, "exp": 3, "ahead": [], "injury_note": None,
            "form": {"games": games, "l3": l3, "beat": 2.0, "pace_gap": pace_gap, "opp": 8.0, "opp_trend": 1.0,
                     "snap_pct": 70, "tgt_snap": 0.1, "ypt": 9.0, "weight": float(games), "games_left": 15}}


def test_row_carries_where_he_sits_and_his_opponents_ranks():
    rank, sched = {"GB": 30, "CHI": 1}, {"MIN": {1: "GB", 2: "CHI"}}
    r = pace.row(_player("A", "WR", 20.0, 100.0), 3, "Some Team", "BE", False, rank, sched, [1, 2])
    assert r["owner"] == "Some Team" and r["slot"] == "BE" and r["mine"] is False
    assert r["opps"] == [(1, "GB", 30), (2, "CHI", 1)]
    fa = pace.row(_player("B", "WR", 20.0, 100.0, team="XXX"), 3, None, "FA", False, rank, sched, [1, 2])
    assert fa["owner"] == "FA" and fa["opps"] == [], "no schedule entry, no opponents rather than a crash"


def test_ranked_filters_short_windows_and_dead_ros_then_sorts_by_pace():
    rows = [pace.row(p, 3, None, "FA", False, {}, {}, [1, 2]) for p in (
        _player("hot", "WR", 20.0, 120.0), _player("hotter", "WR", 25.0, 180.0),
        _player("one-game", "WR", 30.0, 300.0, games=1), _player("nobody", "WR", 9.0, 90.0, ros=20.0),
        _player("rb", "RB", 20.0, 150.0))]
    got = [r["name"] for r in pace.ranked(rows, "WR", top=5, min_games=2, min_adj=60.0)]
    assert got == ["hotter", "hot"], "one-game windows and a ROS under the floor stay out; RBs are another table"
    assert [r["name"] for r in pace.ranked(rows, "WR", top=5, min_games=1, min_adj=60.0)][0] == "one-game"


def test_render_marks_my_players_and_free_agents():
    rows = [pace.row(_player("Mine", "WR", 20.0, 120.0), 3, "My Team", "WR", True, {}, {"MIN": {1: "GB"}}, [1]),
            pace.row(_player("Wire", "WR", 18.0, 100.0), 3, None, "FA", False, {"GB": 30}, {"MIN": {1: "GB"}}, [1])]
    text = pace.render({"week": 3, "ros_week": 3, "min_games": 2, "positions": {"WR": rows}, "rows": rows})
    assert "ME My Team WR" in text and "Wire" in text and " FA " in text and "GB#30" in text
    assert "== my roster" in text and text.count("Mine") == 2, "my players are listed again under the tables, for drops"
