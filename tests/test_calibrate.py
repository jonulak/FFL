"""STRATEGY.md "Usage-adjusted ROS": K is checked, not trusted. The pool builder snapshots what the
blend saw each ROS week; the scorer says which K would have missed least since."""
from ffl import calibrate, db, season


def _row(pid, model, l3, weight, games_left, pos="RB"):
    return {"player_id": pid, "name": f"p{pid}", "pos": pos, "ros_proj": model,
            "form": {"l3": l3, "weight": weight, "games_left": games_left} if weight else None}


def test_snapshot_rows_keep_only_what_the_blend_reads():
    rows = calibrate.snapshot_rows({"RB": [_row(1, 150.0, 12.0, 2.0, 15)], "WR": [_row(2, 90.0, 0, 0, 15, pos="WR")]}, week=3)
    assert rows == [{"player_id": 1, "name": "p1", "pos": "RB", "model": 150.0, "l3": 12.0, "weight": 2.0, "games_left": 15},
                    # no window: nothing recorded his games left, so the bare count from the week
                    {"player_id": 2, "name": "p2", "pos": "WR", "model": 90.0, "l3": None, "weight": 0.0, "games_left": 16}]


def test_write_snapshot_once_per_ros_week():
    rows = calibrate.snapshot_rows({"RB": [_row(1, 150.0, 12.0, 2.0, 15)]}, week=3)
    assert calibrate.write_snapshot(3, rows) is True
    assert calibrate.write_snapshot(3, [{"player_id": 9}]) is False, "the first look of the week stands"
    saved = calibrate.load_snapshots()
    assert saved == [{"week": 3, "rows": rows, "k": season.K_PRIOR}]


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


def test_load_sigma_falls_back_without_a_fit():
    assert calibrate.load_sigma() == (season.SIGMA_FALLBACK, "fallback")


def test_fit_sigma_by_position_skips_thin_and_zero_proj_rows():
    rows = [{"pos": "QB", "pts": 20 + d, "proj": 20} for d in (-6, 6) * 10]
    rows += [{"pos": "K", "pts": 9, "proj": 8}] * 3                                # under MIN_ROWS
    rows += [{"pos": "QB", "pts": 30, "proj": 0}]                                  # no projection
    fit = calibrate.fit_sigma(rows)
    assert fit["n"] == {"QB": 20}
    assert abs(fit["sigma"]["QB"] - 6.16) < 0.01   # sample sd of ±6 over 20 rows
    assert "K" not in fit["sigma"]


def test_brier():
    assert calibrate.brier([(1.0, 1), (0.0, 0)]) == 0.0
    assert calibrate.brier([(0.5, 1), (0.5, 0)]) == 0.25
    assert calibrate.brier([]) is None


def test_write_sigma_round_trips_through_load_sigma():
    calibrate.write_sigma({"sigma": {"WR": 8.2}, "n": {"WR": 120}}, [1, 2, 3])
    sigma, source = calibrate.load_sigma()
    assert source == "calibrated" and sigma["WR"] == 8.2 and sigma["TE"] == season.SIGMA_FALLBACK["TE"]
    assert db.latest_sigma()["weeks"] == [1, 2, 3]


def test_load_sigma_falls_back_on_a_db_error(monkeypatch):
    def boom(*a, **k):
        raise db.DBError("locked")
    monkeypatch.setattr(db, "latest_sigma", boom)
    assert calibrate.load_sigma()[1] == "fallback"
