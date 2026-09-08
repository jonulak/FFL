"""Flattening espn-api objects and merging Sleeper's weekly projections (no network)."""
from types import SimpleNamespace

from ffl import espn_season
from tests.test_season import TWO_QB, roster as season_roster


def bp(**kw):
    base = dict(playerId=1, name="Jahmyr Gibbs", position="RB", proTeam="DET", slot_position="RB",
                on_bye_week=False, injuryStatus="ACTIVE", projected_points=21.4, points=0.0,
                game_played=0, projected_total_points=320.5)
    base.update(kw)
    return SimpleNamespace(**base)


def test_flatten_box_player():
    p = espn_season.flatten_box_player(bp())
    assert p == {"player_id": 1, "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET", "slot": "RB",
                 "bye": False, "injury": "ACTIVE", "proj_espn": 21.4, "proj_sleeper": None, "proj": 21.4,
                 "spread": None, "espn_only": True, "actual": 0.0, "game_played": 0,
                 "season_proj": 320.5, "season_proj_sleeper": None}


def test_flatten_dst_uses_team_position():
    p = espn_season.flatten_box_player(bp(name="Eagles D/ST", position="D/ST", proTeam="PHI", slot_position="D/ST"))
    assert p["pos"] == "D/ST" and p["team"] == "PHI"


def test_flatten_free_agent_without_a_lineup_slot():
    fa = SimpleNamespace(playerId=9, name="Waiver Guy", position="WR", proTeam="NYJ",
                         injuryStatus="ACTIVE", projected_total_points=90.0)
    p = espn_season.flatten_box_player(fa)
    assert p["slot"] == "FA", "lg.free_agents returns Player, which has no slot_position"
    assert p["proj"] == 0.0 and p["season_proj"] == 90.0


def test_merge_weekly_averages_and_flags_misses():
    players = [espn_season.flatten_box_player(bp()),
               espn_season.flatten_box_player(bp(playerId=2, name="A.J. Brown", position="WR", proTeam="PHI", projected_points=15.0)),
               espn_season.flatten_box_player(bp(playerId=3, name="Eagles D/ST", position="D/ST", proTeam="PHI", projected_points=8.0))]
    sl = {("jahmyr gibbs", "RB"): 23.6, ("PHI", "D/ST"): 6.0}
    out = espn_season.merge_weekly(players, sl)
    g, b, d = out
    assert g["proj_sleeper"] == 23.6 and g["proj"] == 22.5 and g["spread"] == 2.2 and g["espn_only"] is False
    assert b["proj_sleeper"] is None and b["proj"] == 15.0 and b["spread"] is None and b["espn_only"] is True
    assert d["proj"] == 7.0, "D/ST merges on team"


def test_merge_weekly_ignores_zero_espn_placeholder():
    players = [espn_season.flatten_box_player(bp(projected_points=0.0))]
    out = espn_season.merge_weekly(players, {("jahmyr gibbs", "RB"): 20.0})
    assert out[0]["proj"] == 20.0


def test_merge_weekly_normalises_dst_team_abbreviation():
    players = [espn_season.flatten_box_player(
        bp(playerId=4, name="Commanders D/ST", position="D/ST", proTeam="WSH", slot_position="D/ST",
           projected_points=8.0))]
    out = espn_season.merge_weekly(players, {("WAS", "D/ST"): 6.0})
    assert out[0]["proj"] == 7.0 and out[0]["espn_only"] is False, "ESPN WSH is Sleeper WAS"


def test_parse_sleeper_rows_skips_players_who_did_not_play():
    rows = [{"player": {"position": "RB", "first_name": "Jahmyr", "last_name": "Gibbs", "team": "DET"},
             "stats": {"gp": 1.0, "rec": 4, "rush_yd": 80, "rush_td": 1}},
            {"player": {"position": "RB", "first_name": "Inactive", "last_name": "Guy", "team": "DET"},
             "stats": {"rec": 0, "rush_yd": 0}}]
    ks = {"ppr": 1, "pass_td": 4, "pass_yd": 0.04, "int": -2, "rush_yd": 0.1, "rush_td": 6,
          "rec_yd": 0.1, "rec_td": 6, "fumble": -2}
    out = espn_season.parse_sleeper_rows(rows, ks)
    assert list(out) == [("jahmyr gibbs", "RB")], "a row with no gp is an inactive all-zero row"
    assert out[("jahmyr gibbs", "RB")] == 18.0


def test_build_side_splits_lineup_bench_ir_and_diffs():
    team = SimpleNamespace(team_id=7, team_name="My Team")
    players = season_roster()
    side = espn_season.build_side(team, players, TWO_QB)
    assert side["team_id"] == 7 and side["team"] == "My Team"
    assert {p["name"] for p in side["ir"]} == {"IR guy"}
    assert {p["name"] for p in side["bench"]} == {"QB C", "RB C", "TE B"}
    assert len(side["lineup"]) == 11
    assert side["optimal_total"] >= side["proj_total"]
    assert any(s["in"] == "QB C" for s in side["optimal_swaps"])
    assert side["actual_total"] == 0.0
