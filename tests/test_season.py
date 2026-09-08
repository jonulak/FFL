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
