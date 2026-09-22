"""STRATEGY.md "Usage-adjusted ROS": K is checked, not trusted. The pool builder snapshots what the
blend saw each ROS week; the scorer says which K would have missed least since."""
import json

from ffl import calibrate, season


def _row(pid, model, l3, weight, games_left, pos="RB"):
    return {"player_id": pid, "name": f"p{pid}", "pos": pos, "ros_proj": model,
            "form": {"l3": l3, "weight": weight, "games_left": games_left} if weight else None}


def test_snapshot_rows_keep_only_what_the_blend_reads():
    rows = calibrate.snapshot_rows({"RB": [_row(1, 150.0, 12.0, 2.0, 15)], "WR": [_row(2, 90.0, 0, 0, 15, pos="WR")]}, week=3)
    assert rows == [{"player_id": 1, "name": "p1", "pos": "RB", "model": 150.0, "l3": 12.0, "weight": 2.0, "games_left": 15},
                    # no window: nothing recorded his games left, so the bare count from the week
                    {"player_id": 2, "name": "p2", "pos": "WR", "model": 90.0, "l3": None, "weight": 0.0, "games_left": 16}]


def test_write_snapshot_once_per_ros_week(tmp_path):
    rows = calibrate.snapshot_rows({"RB": [_row(1, 150.0, 12.0, 2.0, 15)]}, week=3)
    assert calibrate.write_snapshot(tmp_path, 3, rows) is True
    assert calibrate.write_snapshot(tmp_path, 3, [{"player_id": 9}]) is False, "the first look of the week stands"
    saved = json.loads((tmp_path / "week-3.json").read_text())
    assert saved["week"] == 3 and saved["rows"] == rows and saved["k"] == season.K_PRIOR


def test_blend_matches_ros_adj_for_a_healthy_player():
    p = {"ros_proj": 160.0, "injury": "ACTIVE", "form": {"l3": 17.2, "weight": 2.0, "games_left": 15}}
    assert calibrate.blend(160.0, 17.2, 2.0, 15, k=4) == season.ros_adj(p, 3)
    assert calibrate.blend(160.0, None, 0.0, 15, k=4) == 160.0, "no window: the model"


def test_score_ranks_k_by_per_game_error_against_what_happened_since():
    # two players snapshotted at week 3 with 15 games left; each then played 3 games
    rows = [{"player_id": 1, "pos": "RB", "model": 150.0, "l3": 20.0, "weight": 3.0, "games_left": 15},   # hot, stayed hot
            {"player_id": 2, "pos": "RB", "model": 150.0, "l3": 4.0, "weight": 3.0, "games_left": 15}]    # cold, stayed cold
    actual = {1: (57.0, 3), 2: (15.0, 3)}  # (points since, games played since)
    out = calibrate.score([{"week": 3, "rows": rows}], actual, ks=[1, 4, 100])
    assert [o["k"] for o in out] == [1, 4, 100] and all(set(o) >= {"k", "mae", "players"} for o in out)
    assert out[0]["mae"] < out[1]["mae"] < out[2]["mae"], "the window held: a smaller K missed less"
    assert out[0]["players"] == 2
    assert calibrate.score([{"week": 3, "rows": rows}], {}, ks=[4]) == [{"k": 4, "mae": None, "players": 0}], "nobody has played yet"


def test_score_skips_players_without_a_window_or_without_games_since():
    rows = [{"player_id": 1, "pos": "RB", "model": 150.0, "l3": None, "weight": 0.0, "games_left": 15},
            {"player_id": 2, "pos": "RB", "model": 150.0, "l3": 9.0, "weight": 2.0, "games_left": 15}]
    out = calibrate.score([{"week": 3, "rows": rows}], {1: (30.0, 3), 2: (0.0, 0)}, ks=[4])
    assert out == [{"k": 4, "mae": None, "players": 0}]
