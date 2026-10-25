"""the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`), props trial: player props rescored to the league as a third weekly source,
recorded (not used) in weeks 4-6 and scored against ESPN/Sleeper by a bar fixed in advance."""
import math

import pytest

from ffl import props
from tests.scoring import tables

KS = tables({"ppr": 1.0, "pass_td": 4.0, "pass_yd": 0.04, "int": -2.0, "rush_yd": 0.1, "rush_td": 6.0,
      "rec_yd": 0.1, "rec_td": 6.0, "fumble": -2.0})


def _ou(market, who, point, over, under):
    return {"key": market, "outcomes": [{"name": "Over", "description": who, "price": over, "point": point},
                                        {"name": "Under", "description": who, "price": under, "point": point}]}


def _book(key, *markets):
    return {"key": key, "markets": list(markets)}


def test_poisson_mean_over_inverts_the_tail():
    lam = props.poisson_mean_over(1.5, 0.5)
    # P(X >= 2) = 1 - e^-l (1 + l) = 0.5 at l ~ 1.678
    assert abs(1 - math.exp(-lam) * (1 + lam) - 0.5) < 1e-6
    assert props.poisson_mean_over(0.5, 0.6) == pytest.approx(-math.log(0.4), abs=1e-6)


def test_devig_splits_the_overround():
    assert props.devig(1.87, 1.87) == pytest.approx(0.5)
    p = props.devig(1.5, 2.5)  # implied 0.667 + 0.4
    assert p == pytest.approx((1 / 1.5) / (1 / 1.5 + 1 / 2.5))


def test_consensus_takes_the_median_line_and_the_best_td_price():
    event = {"bookmakers": [
        _book("dk", _ou("player_pass_yds", "Patrick Mahomes", 270.5, 1.87, 1.87),
              _ou("player_pass_tds", "Patrick Mahomes", 1.5, 1.87, 1.87),
              {"key": "player_anytime_td", "outcomes": [{"name": "Yes", "description": "Travis Kelce", "price": 2.5}]}),
        _book("fd", _ou("player_pass_yds", "Patrick Mahomes", 280.5, 1.9, 1.9),
              {"key": "player_anytime_td", "outcomes": [{"name": "Yes", "description": "Travis Kelce", "price": 2.8}]}),
        _book("mgm", _ou("player_pass_yds", "Patrick Mahomes", 275.5, 1.9, 1.9)),
    ]}
    got = props.consensus(event)
    assert got["patrick mahomes"]["pass_yd"] == 275.5
    assert got["patrick mahomes"]["pass_td"] == pytest.approx(props.poisson_mean_over(1.5, 0.5))
    # one-sided market: the longest price is the least vig, so it is the one read
    assert got["travis kelce"]["any_td"] == pytest.approx(-math.log(1 - 1 / 2.8))


def test_consensus_devigs_a_two_sided_anytime_market():
    event = {"bookmakers": [_book("x", {"key": "player_anytime_td", "outcomes": [
        {"name": "Yes", "description": "A B", "price": 2.0}, {"name": "No", "description": "A B", "price": 1.8}]})]}
    p = (1 / 2.0) / (1 / 2.0 + 1 / 1.8)
    assert props.consensus(event)["a b"]["any_td"] == pytest.approx(-math.log(1 - p))


def test_props_stats_replace_only_what_the_market_prices():
    sleeper = {"pass_yd": 250.0, "pass_td": 1.8, "pass_int": 0.7, "rush_yd": 20.0, "rush_td": 0.2}
    got = props.props_stats(sleeper, {"pass_yd": 275.5, "any_td": 0.3}, "QB")
    # a QB's anytime TD is his rushing TD; interceptions stay Sleeper's
    assert got == {"pass_yd": 275.5, "pass_td": 1.8, "pass_int": 0.7, "rush_yd": 20.0, "rush_td": 0.3}


def test_props_stats_split_anytime_td_by_sleepers_shares():
    got = props.props_stats({"rush_td": 0.6, "rec_td": 0.2, "rec": 3.0}, {"any_td": 1.0, "rec": 4.5}, "RB")
    assert got["rush_td"] == pytest.approx(0.75) and got["rec_td"] == pytest.approx(0.25) and got["rec"] == 4.5
    # no Sleeper split: a WR's touchdowns are receiving ones
    assert props.props_stats({}, {"any_td": 0.4}, "WR") == {"rec_td": 0.4}


def _player(pid, name, pos, espn, sleeper, **kw):
    return {"player_id": pid, "name": name, "pos": pos, "team": "KC", "proj_espn": espn, "proj_sleeper": sleeper,
            "proj": round((espn + sleeper) / 2, 1) if sleeper is not None else espn, "injury": "ACTIVE",
            "bye": False, "slot": "QB", "kickoff": "2026-10-04T13:00:00-04:00", "fantasy_team": 1, **kw}


def test_snapshot_rows_add_the_props_number_and_the_three_source_mean():
    players = [_player(1, "Patrick Mahomes", "QB", 20.0, 22.0), _player(2, "Nobody Lined", "WR", 5.0, 7.0)]
    stats = {("patrick mahomes", "QB"): {"pass_yd": 250.0, "pass_td": 2.0}, ("nobody lined", "WR"): {"rec": 2.0}}
    rows = props.snapshot_rows(players, stats, {"patrick mahomes": {"pass_yd": 300.0}}, KS)
    m, n = rows
    assert m["proj_props"] == pytest.approx(300 * 0.04 + 2 * 4) and m["markets"] == ["pass_yd"]
    assert m["proj3"] == pytest.approx(round((20.0 + 22.0 + m["proj_props"]) / 3, 1))
    assert n["proj_props"] is None and n["proj3"] == n["proj"], "no line: today's mean"


def _snap(kind, taken_at, rows, week=4):
    return {"week": week, "kind": kind, "taken_at": taken_at, "rows": rows}


def _row(pid, proj, proj3, kickoff="2026-10-04T13:00:00-04:00", props_pts=1.0, **kw):
    return {"player_id": pid, "name": f"p{pid}", "pos": "WR", "proj": proj, "proj3": proj3, "proj_props": props_pts,
            "kickoff": kickoff, "fantasy_team": 2, "slot": "WR", "bye": False, "injury": "ACTIVE", **kw}


def test_pick_rows_reads_the_last_snapshot_before_each_kickoff():
    thu_game = "2026-10-01T20:15:00-04:00"
    thu = _snap("thu", "2026-10-01T18:45:00-04:00", [_row(1, 10, 11, kickoff=thu_game), _row(2, 8, 9)])
    sun = _snap("sun", "2026-10-04T11:30:00-04:00", [_row(1, 10, 10, kickoff=thu_game, props_pts=None), _row(2, 8, 12)])
    picked = {r["player_id"]: r for r in props.pick_rows([sun, thu])}
    assert picked[1]["proj3"] == 11, "the Thursday game is judged off Thursday's lines"
    assert picked[2]["proj3"] == 12, "a Sunday game off Sunday's"


def test_score_compares_errors_only_where_props_had_a_number():
    rows = [_row(1, 10.0, 12.0), _row(2, 8.0, 8.0, props_pts=None), _row(3, 15.0, 13.0), _row(4, 9.0, 9.5)]
    actual = {1: (13.0, 1), 2: (0.0, 1), 3: (20.0, 1), 4: (0.0, 0)}  # 4 never played
    out = props.score_week(rows, actual)
    assert out["players"] == 2
    assert out["mae2"] == pytest.approx((3 + 5) / 2) and out["mae3"] == pytest.approx((1 + 7) / 2)


def test_verdict_needs_the_pool_and_two_of_three_weeks():
    wk = lambda m2, m3, n=100: {"mae2": m2, "mae3": m3, "players": n}
    assert props.verdict([wk(5.0, 4.9), wk(5.0, 4.9), wk(5.0, 5.1)])["passes"] is True
    # pooled better on one big week, but only one week of three
    assert props.verdict([wk(5.0, 3.0), wk(5.0, 5.1), wk(5.0, 5.1)])["passes"] is False
    assert props.verdict([wk(5.0, 4.9), wk(5.0, 4.9)])["passes"] is None, "three weeks are the bar; two is not a verdict"


def test_lineup_flips_price_what_props_would_have_changed():
    settings = {"starters": {"WR": 1}}
    rows = [_row(1, 10.0, 9.0, slot="WR"), _row(2, 9.5, 11.0, slot="BE")]
    flips = props.lineup_flips(rows, {1: (4.0, 1), 2: (15.0, 1)}, settings)
    assert flips == {"in": [2], "out": [1], "gain": 11.0}
    assert props.lineup_flips(rows[:1], {1: (4.0, 1)}, settings) == {"in": [], "out": [], "gain": 0.0}


def test_week_end_stops_before_next_weeks_thursday_game():
    from datetime import datetime, timezone
    thu = datetime(2026, 10, 1, 22, 45, tzinfo=timezone.utc)
    sun = datetime(2026, 10, 4, 15, 30, tzinfo=timezone.utc)
    assert props.week_end(thu) == props.week_end(sun) == datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def test_baseline_is_espn_and_sleeper_only():
    from ffl import espn_season
    from tests.test_espn_season import bp
    out = props.baseline([espn_season.flatten_box_player(bp())], {("jahmyr gibbs", "RB"): 23.6})
    assert out[0]["proj"] == 22.5 and out[0]["proj_sleeper"] == 23.6 and out[0]["proj_espn"] == 21.4
