"""Tests for ffl.rollout: the lineup evaluator, the constrained opponent model, the rollout."""
import numpy as np
import pandas as pd
import pytest

from ffl import board as board_mod
from ffl import draft_watch as dw
from ffl import rollout as ro

from .test_board import EMPTY_ADP, make_players

# The real league's lineup: two QBs, two flex, one of everything else.
SETTINGS_2QB = {
    "team_count": 10,
    "starters": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 2},
    "roster_size": 17,
    "name": "test",
    "season": 2026,
}


def test_lineup_fills_fixed_slots_then_flex_from_the_best_left():
    proj = [300, 280, 200, 190, 180, 250, 240, 230, 150, 100, 120, 110]
    pos = ["QB", "QB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "TE", "D/ST", "K"]
    # QB 300+280, RB 200+190, WR 250+240, TE 150, D/ST 120, K 110, flex = WR 230 + RB 180
    assert ro.lineup_points(proj, pos, SETTINGS_2QB) == 300 + 280 + 200 + 190 + 250 + 240 + 150 + 120 + 110 + 230 + 180


def test_lineup_never_flexes_a_qb_or_counts_a_third_te():
    proj = [300, 280, 270, 200, 190, 250, 240, 150, 140, 130]
    pos = ["QB", "QB", "QB", "RB", "RB", "WR", "WR", "TE", "TE", "TE"]
    # third QB cannot start; flex takes TE 140 + TE 130 (better than nothing), not the QB
    assert ro.lineup_points(proj, pos, SETTINGS_2QB) == 300 + 280 + 200 + 190 + 250 + 240 + 150 + 140 + 130


def test_lineup_with_empty_slots_is_just_what_can_start():
    assert ro.lineup_points([300], ["QB"], SETTINGS_2QB) == 300
    assert ro.lineup_points([], [], SETTINGS_2QB) == 0


def test_season_points_charges_a_shared_bye_more_than_split_byes():
    """Two starting QBs on the same bye lose a QB-week each in the same week; split byes lose
    the same total but a backup can cover one of them. With a QB3 on the roster the shared bye
    still leaves one slot empty; split byes leave none."""
    base_proj = [200, 190, 250, 240, 150, 120, 110]
    base_pos = ["RB", "RB", "WR", "WR", "TE", "D/ST", "K"]
    base_bye = [float("nan")] * 7
    shared = ro.season_points(base_proj + [300, 280, 170], base_pos + ["QB"] * 3,
                              base_bye + [7.0, 7.0, 9.0], SETTINGS_2QB)
    split = ro.season_points(base_proj + [300, 280, 170], base_pos + ["QB"] * 3,
                             base_bye + [7.0, 8.0, 9.0], SETTINGS_2QB)
    assert split > shared
    # and the gap is one QB-week of the backup (the slot nobody can fill), give or take rounding
    assert split - shared == pytest.approx(170 / ro.SEASON_WEEKS, abs=0.01)


def test_season_points_without_byes_equals_full_lineup():
    proj = [300, 280, 200, 190, 250, 240, 150, 120, 110]
    pos = ["QB", "QB", "RB", "RB", "WR", "WR", "TE", "D/ST", "K"]
    assert ro.season_points(proj, pos, [float("nan")] * 9, SETTINGS_2QB) == pytest.approx(sum(proj))


def test_opponents_do_not_draft_a_fourth_qb_or_a_third_te():
    t = ro.Team({"QB": 3, "TE": 2}, picks_left=8)
    ok = ro.allowed_positions(t, SETTINGS_2QB)
    assert "QB" not in ok and "TE" not in ok
    assert {"RB", "WR"} <= ok


def test_opponents_hold_k_and_dst_for_the_run_in():
    early = ro.Team({}, picks_left=10)
    late = ro.Team({"QB": 2, "RB": 4, "WR": 4, "TE": 1}, picks_left=3)
    assert "K" not in ro.allowed_positions(early, SETTINGS_2QB)
    assert "D/ST" not in ro.allowed_positions(early, SETTINGS_2QB)
    assert {"K", "D/ST"} <= ro.allowed_positions(late, SETTINGS_2QB)


def test_opponents_fill_empty_starters_when_the_picks_run_out():
    t = ro.Team({"QB": 2, "RB": 5, "WR": 5, "TE": 1, "K": 1}, picks_left=1)  # only D/ST open
    assert ro.allowed_positions(t, SETTINGS_2QB) == {"D/ST"}


def test_opponent_pick_takes_the_first_usable_player_in_adp_order():
    players = make_players()
    bd = board_mod.build(SETTINGS_2QB, players, EMPTY_ADP)
    pool = ro.Pool.from_board(bd)
    order = np.argsort(pool.adp, kind="stable")
    taken = np.zeros(len(pool), dtype=bool)
    team = ro.Team({"QB": 3}, picks_left=10)
    i = ro.opponent_pick(order, taken, pool, team, SETTINGS_2QB)
    assert pool.pos[i] != "QB"
    # and with nobody taken, it is the first non-QB in the order
    first_non_qb = next(j for j in order if pool.pos[j] != "QB")
    assert i == first_non_qb


def _real_shaped_inputs():
    """Fixture players with an ADP that follows projection, so the rollout has a market to draw from."""
    players = make_players(60)
    players = players[players.pos.isin(["QB", "RB", "WR", "TE"]) | (players.groupby("pos").cumcount() < 12)]
    adp = players.sort_values("proj", ascending=False).reset_index(drop=True)
    adp = pd.DataFrame({"key": adp.name.map(board_mod.norm_name), "pos": adp.pos, "team": adp.team,
                        "adp": np.arange(1, len(adp) + 1, dtype=float),
                        "adp_rank": np.arange(1, len(adp) + 1), "adp_pos_rank": 1,
                        "adp_1qb": np.nan, "adp_1qb_rank": np.nan, "bye": 7.0, "adp_stdev": 6.0})
    return players, adp


def _rec_and_order(bd, settings, me, slot, decide_round=1):
    n = settings["team_count"]
    order = [t for t in range(1, n + 1) if t != me]
    order = order[:slot - 1] + [me] + order[slot - 1:]
    full = dw.snake_order(order, settings["roster_size"])
    decide = next(i + 1 for i, t in enumerate(full) if t == me)
    tm = dw.turn_math(full, me, decide)
    my_roster = bd.head(0)
    rec = dw.recommend(bd, settings, my_roster, 0, tm["my_picks_left"], horizon=tm["horizon"])
    return rec, full, decide, my_roster


def test_rollout_returns_every_candidate_with_a_finite_score():
    players, adp = _real_shaped_inputs()
    bd = board_mod.build(SETTINGS_2QB, players, adp)
    rec, full, decide, my_roster = _rec_and_order(bd, SETTINGS_2QB, me=7, slot=5)
    res = ro.rollout(bd, SETTINGS_2QB, full, 7, decide, {}, my_roster, rec, n_sims=20, seed=1)
    assert res["n_sims"] == 20
    assert len(res["rows"]) >= 8
    for r in res["rows"]:
        assert np.isfinite(r["roll"]) and r["roll"] > 0
    assert res["rows"] == sorted(res["rows"], key=lambda r: -r["roll"])


def test_rollout_finishes_every_simulated_roster_legally():
    """Whatever the noise, my simulated roster must be startable: 2 QB, 1 TE, K, D/ST, and no
    more than the caps at any position."""
    players, adp = _real_shaped_inputs()
    bd = board_mod.build(SETTINGS_2QB, players, adp)
    rec, full, decide, my_roster = _rec_and_order(bd, SETTINGS_2QB, me=7, slot=10)
    pool = ro.Pool.from_board(bd)
    rng = np.random.default_rng(3)
    remaining = full[decide - 1:]
    from collections import Counter
    picks_left = Counter(remaining)
    for _ in range(5):
        order = np.argsort(pool.adp + rng.normal(0, 1, len(pool)) * pool.adp_sd, kind="stable")
        taken = np.zeros(len(pool), dtype=bool)
        teams = {t: ro.Team({}, n) for t, n in picks_left.items()}
        mine = []
        for k, t in enumerate(remaining):
            team = teams[t]
            if t == 7:
                i = ro.my_pick(taken, pool, mine, team, SETTINGS_2QB)
                mine.append(i)
            else:
                i = ro.opponent_pick(order, taken, pool, team, SETTINGS_2QB)
            taken[i] = True
            team.counts[pool.pos[i]] = team.counts.get(pool.pos[i], 0) + 1
            team.picks_left -= 1
        counts = Counter(pool.pos[i] for i in mine)
        assert counts["QB"] >= 2 and counts["TE"] >= 1 and counts["K"] == 1 and counts["D/ST"] == 1
        assert counts["RB"] >= 2 and counts["WR"] >= 2
        for p, cap in ro.ROSTER_CAP.items():
            assert counts[p] <= cap, counts
        for t, team in teams.items():
            assert team.picks_left == 0


def test_rollout_time_budget_stops_early_but_reports_what_it_did():
    players, adp = _real_shaped_inputs()
    bd = board_mod.build(SETTINGS_2QB, players, adp)
    rec, full, decide, my_roster = _rec_and_order(bd, SETTINGS_2QB, me=7, slot=1)
    res = ro.rollout(bd, SETTINGS_2QB, full, 7, decide, {}, my_roster, rec, n_sims=10_000,
                     time_budget_s=0.5, seed=1)
    assert 20 <= res["n_sims"] < 10_000


def test_rollout_lines_flag_a_disagreement_outside_the_score_top_five():
    rec = {"top": [{"player_id": i, "name": f"P{i}", "pos": "RB", "score": 10 - i} for i in range(10)]}
    res = {"rows": [{"player_id": 9, "name": "P9", "pos": "WR", "roll": 1500.0, "roll_se": 2.0, "gap_se": 0.0, "next_pos": "RB", "next_proj": 200},
                    {"player_id": 0, "name": "P0", "pos": "RB", "roll": 1490.0, "roll_se": 2.0, "gap_se": 1.0, "next_pos": "WR", "next_proj": 210}],
           "n_sims": 100, "elapsed": 1.0}
    lines = ro.rollout_lines(res, rec)
    assert lines[0].startswith("rollout")
    assert any("ROLLOUT DISAGREES" in l and "P9" in l for l in lines)
    res["rows"].reverse()
    res["rows"][0]["roll"], res["rows"][1]["roll"] = 1500.0, 1490.0
    assert not any("DISAGREES" in l for l in ro.rollout_lines(res, rec))


def test_rollout_is_off_unless_asked_for(monkeypatch):
    monkeypatch.delenv("FFL_ROLLOUT", raising=False)
    assert not ro.enabled()
    monkeypatch.setenv("FFL_ROLLOUT", "0")
    assert not ro.enabled()
    monkeypatch.setenv("FFL_ROLLOUT", "1")
    assert ro.enabled()


def test_verdict_line_reads_the_chosen_pick_against_the_leader():
    res = {"rows": [{"player_id": 1, "name": "A", "pos": "RB", "roll": 100.0, "gap_se": 0.0},
                    {"player_id": 2, "name": "B", "pos": "WR", "roll": 95.0, "gap_se": 1.2}],
           "n_sims": 50, "elapsed": 1.0}
    assert ro.verdict_line(res, 1).startswith("rollout: agrees")
    assert "-5.0" in ro.verdict_line(res, 2) and "A RB leads" in ro.verdict_line(res, 2)
    assert "not a candidate" in ro.verdict_line(res, 99)


def test_candidates_never_add_a_position_at_its_cap():
    players, adp = _real_shaped_inputs()
    bd = board_mod.build(SETTINGS_2QB, players, adp)
    pool = ro.Pool.from_board(bd)
    rec = {"top": []}
    # two TEs rostered, TE slot filled, flex open: the flex must not put a third TE on the list
    cands = ro.candidate_indices(pool, rec, {"TE": 2, "QB": 2}, SETTINGS_2QB)
    assert cands and all(pool.pos[i] != "TE" for i in cands)
    assert {pool.pos[i] for i in cands} >= {"RB", "WR"}


def test_assignment_matches_brute_force():
    import itertools
    rng = np.random.default_rng(0)
    for _ in range(30):
        n_rows, n_cols = int(rng.integers(1, 5)), int(rng.integers(1, 6))
        v = rng.normal(size=(n_rows, n_cols))
        col = ro.assign_max(v)
        got = sum(v[r, c] for r, c in enumerate(col) if c >= 0)
        assert len({c for c in col if c >= 0}) == sum(1 for c in col if c >= 0)  # distinct columns
        best = -1e9
        k = min(n_rows, n_cols)
        for rows in itertools.combinations(range(n_rows), k):
            for cols in itertools.permutations(range(n_cols), k):
                best = max(best, sum(v[r, c] for r, c in zip(rows, cols)))
        assert got == pytest.approx(best)


def test_expected_at_picks_walks_the_adp_tail():
    players, adp = _real_shaped_inputs()
    bd = board_mod.build(SETTINGS_2QB, players, adp)
    pool = ro.Pool.from_board(bd)
    taken = np.zeros(len(pool), dtype=bool)
    e = ro.expected_at_picks(pool, taken, "QB", [1, 20, 2000], depth=2)
    assert e.shape == (3, 2)
    assert e[0, 0] >= e[1, 0] >= e[2, 0]           # later picks, worse players
    assert e[0, 0] >= e[0, 1]                      # best before second best
    assert e[2, 0] == pytest.approx(pool.repl["QB"])  # nobody left: replacement


def test_plan_takes_the_position_whose_wait_is_dearest():
    """Two open slots, two picks. At the second pick the RB left is far worse than the WR left,
    so the plan must fill RB now and WR later."""
    rows = []
    pid = 1
    for i, (proj, adp) in enumerate([(300, 1), (200, 30), (150, 60)]):
        rows.append({"player_id": pid, "name": f"RB{i}", "pos": "RB", "team": "T", "proj": proj,
                     "injury": "ACTIVE", "adp": adp})
        pid += 1
    for i, (proj, adp) in enumerate([(290, 2), (280, 30), (270, 60)]):
        rows.append({"player_id": pid, "name": f"WR{i}", "pos": "WR", "team": "T", "proj": proj,
                     "injury": "ACTIVE", "adp": adp})
        pid += 1
    df = pd.DataFrame(rows)
    df["drafted"] = False
    df["bye"] = np.nan
    df["repl"] = 100.0
    pool = ro.Pool.from_board(df)
    st = {"team_count": 2, "starters": {"RB": 1, "WR": 1}, "roster_size": 2, "name": "t", "season": 2026}
    team = ro.Team({}, picks_left=2)
    assert ro.plan_position(pool, np.zeros(len(pool), bool), team, st, [5, 25]) == "RB"
