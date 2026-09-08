"""Pure in-season maths: lineup-optimal, waiver deltas, the decisions append."""
import pytest

from ffl import season

TWO_QB = {"team_count": 10,
          "starters": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 2}}


def P(pid, name, pos, proj, slot="BE", injury="ACTIVE", bye=False, team="T", season_proj=None):
    return {"player_id": pid, "name": name, "pos": pos, "team": team, "slot": slot, "bye": bye,
            "injury": injury, "proj_espn": proj, "proj_sleeper": proj, "proj": proj, "spread": 0.0,
            "espn_only": False, "actual": 0.0, "game_played": 0,
            "season_proj": season_proj if season_proj is not None else proj * 17,
            "season_proj_sleeper": None}


def roster():
    return [
        P(1, "QB A", "QB", 20, "QB"), P(2, "QB B", "QB", 18, "QB"), P(3, "QB C", "QB", 22, "BE"),
        P(4, "RB A", "RB", 15, "RB"), P(5, "RB B", "RB", 14, "RB"), P(6, "RB C", "RB", 16, "BE"),
        P(7, "WR A", "WR", 13, "WR"), P(8, "WR B", "WR", 12, "WR"), P(9, "WR C", "WR", 9, "RB/WR/TE"),
        P(10, "TE A", "TE", 8, "TE"), P(11, "TE B", "TE", 7, "BE"),
        P(12, "RB D", "RB", 10, "RB/WR/TE"),
        P(13, "DST", "D/ST", 6, "D/ST"), P(14, "K", "K", 7, "K"),
        P(15, "IR guy", "RB", 30, "IR", injury="INJURY_RESERVE"),
    ]


def by_slot(lineup):
    return {(e["slot"], i): e["name"] for i, e in enumerate(lineup)}


def test_optimal_lineup_fills_fixed_slots_then_flex_and_ignores_ir():
    lu = season.optimal_lineup(roster(), TWO_QB)
    assert [e["slot"] for e in lu] == ["QB", "QB", "RB", "RB", "WR", "WR", "TE", "RB/WR/TE", "RB/WR/TE", "D/ST", "K"]
    names = [e["name"] for e in lu]
    assert names[:2] == ["QB C", "QB A"], "best two QBs start; the 22-pt bench QB is in"
    assert names[2:4] == ["RB C", "RB A"]
    assert set(names[7:9]) == {"RB B", "RB D"}, "flex takes the best remaining RB/WR/TE: RB B 14, RB D 10"


def test_flex_takes_the_best_remaining_eligible():
    lu = season.optimal_lineup(roster(), TWO_QB)
    flex = {e["name"] for e in lu if e["slot"] == "RB/WR/TE"}
    assert flex == {"RB B", "RB D"}, "RB B 14 and RB D 10 beat WR C 9 and TE B 7"
    assert "IR guy" not in [e["name"] for e in lu]


def test_out_and_bye_players_are_not_started():
    r = roster()
    r[5]["injury"] = "OUT"          # RB C
    r[3]["bye"] = True              # RB A
    lu = season.optimal_lineup(r, TWO_QB)
    starters = [e["name"] for e in lu]
    assert "RB C" not in starters and "RB A" not in starters
    assert season.playable_proj(r[5]) == 0.0


def test_empty_slot_when_nobody_is_eligible():
    r = [p for p in roster() if p["pos"] != "K"]
    lu = season.optimal_lineup(r, TWO_QB)
    k = next(e for e in lu if e["slot"] == "K")
    assert k["player_id"] is None and k["proj"] == 0.0


def test_lineup_diff_lists_swaps_with_gain():
    cur = season.current_lineup(roster())
    opt = season.optimal_lineup(roster(), TWO_QB)
    diff = season.lineup_diff(cur, opt)
    assert {(d["out"], d["in"]) for d in diff} >= {("QB B", "QB C")}
    qb = next(d for d in diff if d["in"] == "QB C")
    assert qb["gain"] == pytest.approx(4.0) and qb["slot"] == "QB"
    assert diff == sorted(diff, key=lambda d: -d["gain"])
    assert season.lineup_total(opt) >= season.lineup_total(cur)


def test_lineup_diff_is_empty_when_already_optimal():
    opt = season.optimal_lineup(roster(), TWO_QB)
    assert season.lineup_diff(opt, opt) == []


def test_ros_fraction_is_one_in_week_one_and_zero_after_the_season():
    assert season.ros_fraction(1) == 1.0
    assert season.ros_fraction(18) == 0.0
    assert season.ros_fraction(10) == pytest.approx(8 / 17)
    assert season.ros_proj(170.0, 10) == pytest.approx(80.0)


def test_injury_factors():
    assert season.injury_terms("ACTIVE", 1) == (1.0, 1.0)
    assert season.injury_terms("QUESTIONABLE", 1) == (0.85, 1.0)
    assert season.injury_terms("DOUBTFUL", 1) == (0.7, 1.0)
    f, avail = season.injury_terms("OUT", 1)
    assert f == 1.0 and avail == pytest.approx(16 / 17)
    f, avail = season.injury_terms("INJURY_RESERVE", 1)
    assert avail == pytest.approx(13 / 17)
    f, avail = season.injury_terms("INJURY_RESERVE", 17)
    assert avail == 0.0, "no games left after return"


def test_add_score_formula():
    # healthy, would start: (ros + week) × 1
    assert season.add_score(30.0, 4.0, "ACTIVE", 1) == pytest.approx(34.0)
    # questionable: × 0.85
    assert season.add_score(30.0, 4.0, "QUESTIONABLE", 1) == pytest.approx(28.9)
    # out: no week term, ros scaled to the games after return
    assert season.add_score(34.0, 4.0, "OUT", 1) == pytest.approx(34.0 * 16 / 17)


def fa(pid, name, pos, week_proj, season_proj, injury="ACTIVE"):
    return P(pid, name, pos, week_proj, "FA", injury=injury, season_proj=season_proj)


def test_waiver_deltas_against_roster_and_pool():
    mine = roster()
    frees = [fa(100, "FA RB hot", "RB", 17, 200), fa(101, "FA RB cold", "RB", 5, 60),
             fa(102, "FA QB", "QB", 19, 300)]
    # a big free pool so replacement is well below my players
    filler = [fa(200 + i, f"fill RB{i}", "RB", 3, 40 - i) for i in range(40)]
    pool = mine + frees + filler
    rows = season.waiver_deltas(frees, mine, pool, TWO_QB, week=1)
    hot = next(r for r in rows if r["name"] == "FA RB hot")
    cold = next(r for r in rows if r["name"] == "FA RB cold")
    # my weakest non-IR RB by ROS is RB D (10*17 = 170); hot gains 30 vs roster
    assert hot["delta_roster"] == pytest.approx(200 - 170)
    assert hot["would_start"] is True and hot["week_delta"] == pytest.approx(17 - 10), "displaces RB D (10) in flex"
    assert hot["add"] == pytest.approx(30 + 7)
    assert cold["would_start"] is False and cold["week_delta"] == 0.0
    assert cold["delta_roster"] < 0
    assert rows[0]["name"] == "FA RB hot" or rows[0]["name"] == "FA QB"
    assert [r["add"] for r in rows] == sorted((r["add"] for r in rows), reverse=True)
    assert set(rows[0]) >= {"ros_proj", "week_proj", "delta_roster", "delta_pool", "would_start", "week_delta", "add"}


def test_delta_uses_the_higher_of_roster_and_pool_replacement():
    mine = roster()
    frees = [fa(100, "FA RB", "RB", 12, 150)]
    # the pool is only my roster + this FA: replacement index lands high, above my weakest
    rows = season.waiver_deltas(frees, mine, mine + frees, TWO_QB, week=1)
    r = rows[0]
    assert r["delta_pool"] != r["delta_roster"]
    assert r["add"] == pytest.approx(min(r["delta_roster"], r["delta_pool"]) + r["week_delta"])
