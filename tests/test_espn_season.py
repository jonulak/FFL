"""Flattening espn-api objects and merging Sleeper's weekly projections (no network)."""
from types import SimpleNamespace

from ffl import espn_season


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
