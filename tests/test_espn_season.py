"""Flattening espn-api objects and merging Sleeper's weekly projections (no network)."""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import requests

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
                 "season_proj": 320.5, "season_proj_sleeper": None,
                 "return_week": None, "injury_note": None}


def test_flatten_dst_uses_team_position():
    p = espn_season.flatten_box_player(bp(name="Eagles D/ST", position="D/ST", proTeam="PHI", slot_position="D/ST"))
    assert p["pos"] == "D/ST" and p["team"] == "PHI"


def test_flatten_free_agent_keeps_its_weekly_projection():
    fa = SimpleNamespace(playerId=9, name="Waiver Guy", position="WR", proTeam="NYJ",
                         injuryStatus="ACTIVE", projected_points=11.5, projected_total_points=90.0)
    p = espn_season.flatten_box_player(fa)
    assert p["slot"] == "FA", "BoxPlayer defaults slot_position to FA; the getattr covers a plain Player"
    assert p["proj"] == 11.5 and p["season_proj"] == 90.0


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


SEASON_ESPN = [{"player_id": 1, "name": "Jalen Hurts", "pos": "QB", "team": "PHI", "proj": 380.0},
               {"player_id": 2, "name": "No Sleeper Row", "pos": "WR", "team": "NYJ", "proj": 100.0},
               {"player_id": 3, "name": "Commanders D/ST", "pos": "D/ST", "team": "WSH", "proj": 90.0},
               {"player_id": 4, "name": "Zero Guy", "pos": "TE", "team": "DAL", "proj": 0.0}]
SEASON_SLEEPER = [{"key": "jalen hurts", "pos": "QB", "team": "PHI", "proj_sleeper": 360.0},
                  {"key": "commanders dst", "pos": "D/ST", "team": "WAS", "proj_sleeper": 80.0},
                  {"key": "zero guy", "pos": "TE", "team": "DAL", "proj_sleeper": 150.0}]


def test_merge_season_averages_sources_and_matches_dst_on_team():
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER)
    assert out[1] == {"season_proj": 370.0, "season_proj_sleeper": 360.0}
    assert out[2] == {"season_proj": 100.0, "season_proj_sleeper": None}, "a Sleeper miss keeps ESPN alone"
    assert out[3]["season_proj"] == 85.0, "ESPN WSH D/ST matches Sleeper WAS on team"
    assert out[4]["season_proj"] == 150.0, "an ESPN 0 is a placeholder, not a projection"


def test_merge_season_takes_a_dataframe_too():
    out = espn_season.merge_season(SEASON_ESPN, pd.DataFrame(SEASON_SLEEPER))
    assert out[1]["season_proj"] == 370.0 and out[3]["season_proj"] == 85.0


def test_apply_season_overrides_only_the_ids_it_knows():
    players = [espn_season.flatten_box_player(bp(playerId=1)),
               espn_season.flatten_box_player(bp(playerId=99))]
    out = espn_season.apply_season(players, {1: {"season_proj": 370.0, "season_proj_sleeper": 360.0}})
    assert out[0]["season_proj"] == 370.0 and out[0]["season_proj_sleeper"] == 360.0
    assert out[1]["season_proj"] == 320.5, "an unknown id keeps the flattened fallback"
    assert players[0]["season_proj"] == 320.5, "pure: the input list is untouched"


def as_box(p):
    """A roster dict from tests.test_season back as the espn-api object flatten reads."""
    return SimpleNamespace(playerId=p["player_id"], name=p["name"], position=p["pos"],
                           proTeam=p["team"], slot_position=p["slot"], on_bye_week=p["bye"],
                           injuryStatus=p["injury"], projected_points=p["proj"], points=p["actual"],
                           game_played=0, projected_total_points=p["season_proj"])


def test_week_state_gives_no_opponent_on_a_bye(monkeypatch):
    """A placeholder side carries no team_id; the week still builds, with opp None."""
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in season_roster()],
                              away_team=SimpleNamespace(), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.week_state(week=1, sleeper={}, season={}, injuries={})
    assert st["week"] == 1 and st["opp"] is None
    assert "stale" not in st, "the freshness flag is stale_since, added by the API's cache"
    assert st["me"]["team"] == "Mine" and len(st["me"]["lineup"]) == 11


class FakeLeagueFactory:
    """Stands in for config.league: an lru_cache'd callable with a cache_clear()."""

    def __init__(self):
        self.builds = 0
        self.cleared = 0
        self.cached = None

    def __call__(self):
        if self.cached is None:
            self.builds += 1
            self.cached = SimpleNamespace(current_week=self.builds)
        return self.cached

    def cache_clear(self):
        self.cleared += 1
        self.cached = None


def test_fresh_league_reuses_the_league_inside_the_ttl(monkeypatch):
    fake = FakeLeagueFactory()
    monkeypatch.setattr(espn_season, "league", fake)
    monkeypatch.setattr(espn_season, "_LEAGUE_BUILT_AT", 0.0)
    now = [1_000_000.0]
    monkeypatch.setattr(espn_season.time, "time", lambda: now[0])
    assert espn_season.fresh_league().current_week == 1
    now[0] += espn_season.LEAGUE_TTL_S - 1
    assert espn_season.fresh_league().current_week == 1
    assert fake.builds == 1 and fake.cleared == 1, "one build, one clear on the very first call"


def test_fresh_league_rebuilds_after_the_ttl(monkeypatch):
    """espn_api reads current_week in League.__init__, so a launchd process that lives for
    months would sit on the boot week forever without this."""
    fake = FakeLeagueFactory()
    monkeypatch.setattr(espn_season, "league", fake)
    monkeypatch.setattr(espn_season, "_LEAGUE_BUILT_AT", 0.0)
    now = [1_000_000.0]
    monkeypatch.setattr(espn_season.time, "time", lambda: now[0])
    assert espn_season.fresh_league().current_week == 1
    now[0] += espn_season.LEAGUE_TTL_S + 1
    assert espn_season.fresh_league().current_week == 2
    assert fake.builds == 2


def test_refresh_league_forces_the_next_build(monkeypatch):
    fake = FakeLeagueFactory()
    monkeypatch.setattr(espn_season, "league", fake)
    monkeypatch.setattr(espn_season, "_LEAGUE_BUILT_AT", 0.0)
    espn_season.fresh_league()
    espn_season.refresh_league()
    assert espn_season.fresh_league().current_week == 2, "POST /refresh must not wait out the TTL"


INJ_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "espn_injuries.json").read_text())
WEEK1 = date(2026, 9, 8)  # the Tuesday settings.nfl_week1_start(2026) returns
LOVE, BRISSETT, CARTER, SENN, PENIX = 4870808, 2578570, 4240619, 4428458, 4360423


def test_parse_injuries_keys_on_the_player_card_link():
    inj = espn_season.parse_injuries(INJ_FIXTURE)
    assert set(inj) == {LOVE, BRISSETT, CARTER, SENN, PENIX}, "the row with no links is dropped"
    assert inj[CARTER]["return_date"] == date(2026, 10, 11) and inj[CARTER]["body_part"] == "Knee"
    assert inj[CARTER]["note"].startswith("The Cardinals placed Carter (knee) on injured reserve")


def test_parse_injuries_keeps_active_rows_and_survives_missing_details():
    inj = espn_season.parse_injuries(INJ_FIXTURE)
    assert inj[BRISSETT]["return_date"] is None and inj[BRISSETT]["body_part"] is None
    assert "depth chart" in inj[BRISSETT]["note"], "an Active row's comment is still real news"
    assert inj[SENN]["note"] is None, "both comments blank is no note at all"


def test_parse_injuries_falls_back_to_the_long_comment():
    row = {"shortComment": "  ", "longComment": "The long one.",
           "athlete": {"links": [{"href": "https://www.espn.com/nfl/player/_/id/42/x"}]}}
    inj = espn_season.parse_injuries({"injuries": [{"displayName": "T", "injuries": [row]}]})
    assert inj[42]["note"] == "The long one."


def test_apply_injuries_sets_the_return_week_and_note():
    players = [espn_season.flatten_box_player(bp(playerId=CARTER)),
               espn_season.flatten_box_player(bp(playerId=BRISSETT)),
               espn_season.flatten_box_player(bp(playerId=99))]
    out = espn_season.apply_injuries(players, espn_season.parse_injuries(INJ_FIXTURE), WEEK1)
    assert out[0]["return_week"] == 5, "2026-10-11 is week 5 off a 2026-09-08 week 1"
    assert out[1]["return_week"] is None and out[1]["injury_note"].startswith("Brissett")
    assert out[2] == players[2], "an id the feed does not carry is untouched"
    assert players[0]["return_week"] is None, "pure: the input list is untouched"


def test_apply_injuries_without_week1_start_keeps_only_the_notes():
    """A settings.json written before ffl.settings added week1_start: no date maths is possible."""
    players = [espn_season.flatten_box_player(bp(playerId=CARTER))]
    out = espn_season.apply_injuries(players, espn_season.parse_injuries(INJ_FIXTURE), None)
    assert out[0]["return_week"] is None and out[0]["injury_note"] is not None


def test_week_state_attaches_injuries_to_both_sides(monkeypatch):
    mine = season_roster()
    mine[0]["player_id"] = CARTER
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in mine],
                              away_team=SimpleNamespace(team_id=8, team_name="Theirs"),
                              away_lineup=[as_box(p) for p in season_roster()])
    monkeypatch.setattr(espn_season, "league", lambda: SimpleNamespace(box_scores=lambda week: [matchup], current_week=1))
    monkeypatch.setattr(espn_season, "load", lambda: {**TWO_QB, "week1_start": WEEK1.isoformat()})
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.week_state(week=1, sleeper={}, season={},
                               injuries=espn_season.parse_injuries(INJ_FIXTURE))
    hurt = next(p for p in st["me"]["lineup"] if p["player_id"] == CARTER)
    assert hurt["return_week"] == 5 and hurt["injury_note"]
    assert all(p["return_week"] is None for p in st["opp"]["lineup"]), "the opponent side gets the keys too"


def test_fetch_injuries_serves_the_last_good_data_then_an_empty_dict(monkeypatch):
    """ESPN going down must not blank the notes: season.injury_terms' constants are the last resort."""
    espn_season._INJ.clear()
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) == 1:
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: INJ_FIXTURE)
        raise requests.ConnectionError("espn down")

    monkeypatch.setattr(espn_season.requests, "get", fake_get)
    monkeypatch.setattr(espn_season.time, "time", lambda: 1_000_000.0)
    good = espn_season.fetch_injuries()
    assert CARTER in good
    assert espn_season.fetch_injuries() is good and len(calls) == 1, "cached inside CACHE_S"
    monkeypatch.setattr(espn_season.time, "time", lambda: 1_000_000.0 + espn_season.CACHE_S + 1)
    assert espn_season.fetch_injuries() == good, "expired and ESPN down: the stale data still serves"
    espn_season._INJ.clear()
    assert espn_season.fetch_injuries() == {}, "with nothing cached there is nothing to serve"
