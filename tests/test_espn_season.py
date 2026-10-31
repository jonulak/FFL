"""Flattening espn-api objects and merging Sleeper's weekly projections (no network)."""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from ffl import espn_season, season
from ffl.adp import norm_name
from tests.test_season import TWO_QB, roster as season_roster
from tests.scoring import tables
from tests.home import home_league


def bp(**kw):
    base = dict(playerId=1, name="Jahmyr Gibbs", position="RB", proTeam="DET", slot_position="RB",
                on_bye_week=False, injuryStatus="ACTIVE", projected_points=21.4, points=0.0,
                game_played=0, projected_total_points=320.5)
    base.update(kw)
    return SimpleNamespace(**base)


def test_flatten_box_player():
    p = espn_season.flatten_box_player(bp())
    assert p == {"player_id": 1, "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET", "slot": "RB",
                 "bye": False, "injury": "ACTIVE", "proj_espn": 21.4, "proj": 21.4, "spread": None, "n_src": 1,
                 "proj_src": {"espn": 21.4}, "actual": 0.0, "game_played": 0, "season_proj": 320.5,
                 "ros_proj": None, "ros_n": None, "ros_spread": None,
                 "return_week": None, "injury_note": None, "exp": None, "ahead": [], "opponent": None,
                 "kickoff": None, "home": None}


def test_flatten_dst_uses_team_position():
    p = espn_season.flatten_box_player(bp(name="Eagles D/ST", position="D/ST", proTeam="PHI", slot_position="D/ST"))
    assert p["pos"] == "D/ST" and p["team"] == "PHI"


def test_flatten_free_agent_keeps_its_weekly_projection():
    fa = SimpleNamespace(playerId=9, name="Waiver Guy", position="WR", proTeam="NYJ",
                         injuryStatus="ACTIVE", projected_points=11.5, projected_total_points=90.0)
    p = espn_season.flatten_box_player(fa)
    assert p["slot"] == "FA", "BoxPlayer defaults slot_position to FA; the getattr covers a plain Player"
    assert p["proj"] == 11.5 and p["season_proj"] == 90.0


def test_flatten_carries_opponent_and_a_local_iso_kickoff():
    from datetime import datetime
    p = espn_season.flatten_box_player(bp(pro_opponent="GB", game_date=datetime(2026, 9, 13, 13, 0)))
    assert p["opponent"] == "GB"
    assert p["kickoff"] == datetime(2026, 9, 13, 13, 0).astimezone().isoformat()


def test_flatten_bye_has_no_opponent_or_kickoff():
    from datetime import datetime
    p = espn_season.flatten_box_player(bp(on_bye_week=True, pro_opponent="None", game_date=datetime(2026, 9, 13, 13, 0)))
    assert p["opponent"] is None and p["kickoff"] is None
    q = espn_season.flatten_box_player(bp(pro_opponent="None"))
    assert q["opponent"] is None, 'espn-api writes the string "None" when it has no schedule'


def test_merge_weekly_takes_the_consensus_and_drops_the_espn_input():
    players = [espn_season.flatten_box_player(bp()),
               espn_season.flatten_box_player(bp(playerId=2, name="A.J. Brown", position="WR", proTeam="PHI", projected_points=15.0)),
               espn_season.flatten_box_player(bp(playerId=3, name="Eagles D/ST", position="D/ST", proTeam="PHI", projected_points=8.0))]
    src = {"sleeper": {("jahmyr gibbs", "RB"): 23.6, ("PHI", "D/ST"): 6.0},
           "cbs": {("jahmyr gibbs", "RB"): 20.0}, "sharks": {("jahmyr gibbs", "RB"): 30.0}, "fleaflicker": {}}
    g, b, d = espn_season.merge_weekly(players, src)
    # Walsh averages of 20, 21.4, 23.6, 30: median (22.5 + 23.6) / 2; spread 23.6 - 21.4 after the trim
    assert g["n_src"] == 4 and g["proj"] == pytest.approx(23.05, abs=0.06) and g["spread"] == 2.2
    assert b["proj"] == 15.0 and b["n_src"] == 1 and b["spread"] is None, "ESPN alone: his number stands"
    assert d["proj"] == 7.0 and d["n_src"] == 2, "D/ST merges on team"
    assert list(g["proj_src"].items()) == [("sharks", 30.0), ("sleeper", 23.6), ("espn", 21.4), ("cbs", 20.0)]
    assert b["proj_src"] == {"espn": 15.0}
    assert all("proj_espn" not in p and "proj_sleeper" not in p and "espn_only" not in p for p in (g, b, d))


def test_merge_weekly_two_sources_is_the_old_mean():
    out = espn_season.merge_weekly([espn_season.flatten_box_player(bp())], {"sleeper": {("jahmyr gibbs", "RB"): 23.6}})
    assert out[0]["proj"] == 22.5 and out[0]["spread"] == 2.2


def test_merge_weekly_ignores_zero_espn_placeholder():
    out = espn_season.merge_weekly([espn_season.flatten_box_player(bp(projected_points=0.0))],
                                   {"sleeper": {("jahmyr gibbs", "RB"): 20.0}})
    assert out[0]["proj"] == 20.0 and out[0]["n_src"] == 1
    none = espn_season.merge_weekly([espn_season.flatten_box_player(bp(projected_points=0.0))], {})
    assert none[0]["proj"] == 0.0 and none[0]["n_src"] == 0


def test_merge_weekly_normalises_dst_team_abbreviation():
    players = [espn_season.flatten_box_player(
        bp(playerId=4, name="Commanders D/ST", position="D/ST", proTeam="WSH", slot_position="D/ST", projected_points=8.0))]
    out = espn_season.merge_weekly(players, {"sleeper": {("WAS", "D/ST"): 6.0}})
    assert out[0]["proj"] == 7.0 and out[0]["n_src"] == 2, "ESPN WSH is Sleeper WAS"


def test_weekly_sources_survive_a_source_failing(monkeypatch):
    def boom(week, ks):
        raise requests.ConnectionError("x")
    monkeypatch.setattr(espn_season, "_sleeper_for", boom)
    monkeypatch.setattr(espn_season, "WEEKLY_SOURCES", {"cbs": lambda w, ks: {("a", "RB"): 1.0}})
    out = espn_season.weekly_sources(5, {"scoring": []})
    assert out == {"sleeper": {}, "cbs": {("a", "RB"): 1.0}}


def test_parse_sleeper_rows_skips_players_who_did_not_play():
    rows = [{"player": {"position": "RB", "first_name": "Jahmyr", "last_name": "Gibbs", "team": "DET"},
             "stats": {"gp": 1.0, "rec": 4, "rush_yd": 80, "rush_td": 1}},
            {"player": {"position": "RB", "first_name": "Inactive", "last_name": "Guy", "team": "DET"},
             "stats": {"rec": 0, "rush_yd": 0}}]
    ks = tables({"ppr": 1, "pass_td": 4, "pass_yd": 0.04, "int": -2, "rush_yd": 0.1, "rush_td": 6,
                 "rec_yd": 0.1, "rec_td": 6, "fumble": -2})
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
    assert side["owner"] == ""  # no owners on the team: no name, not a crash


def test_owner_name_trims_capitalises_and_joins_co_owners():
    # as ESPN stores them in this league: a trailing space, a lower-case name, a handle never shown
    one = SimpleNamespace(owners=[{"firstName": "Henry", "lastName": "Chiem ", "displayName": "ESPNFAN2560229613"}])
    low = SimpleNamespace(owners=[{"firstName": "henry", "lastName": "kubacki"}])
    two = SimpleNamespace(owners=[{"firstName": "Anthony", "lastName": "Arena"}, {"firstName": "Dan", "lastName": None}])
    assert espn_season.owner_name(one) == "Henry Chiem"
    assert espn_season.owner_name(low) == "Henry Kubacki"
    assert espn_season.owner_name(two) == "Anthony Arena & Dan"
    assert espn_season.owner_name(SimpleNamespace(owners=[{}])) == ""


SEASON_ESPN = [{"player_id": 1, "name": "Jalen Hurts", "pos": "QB", "team": "PHI", "proj": 380.0},
               {"player_id": 2, "name": "No Sleeper Row", "pos": "WR", "team": "NYJ", "proj": 100.0},
               {"player_id": 3, "name": "Commanders D/ST", "pos": "D/ST", "team": "WSH", "proj": 90.0},
               {"player_id": 4, "name": "Zero Guy", "pos": "TE", "team": "DAL", "proj": 0.0}]
SEASON_SLEEPER = [{"key": "jalen hurts", "pos": "QB", "team": "PHI", "proj_sleeper": 360.0},
                  {"key": "commanders dst", "pos": "D/ST", "team": "WAS", "proj_sleeper": 80.0},
                  {"key": "zero guy", "pos": "TE", "team": "DAL", "proj_sleeper": 150.0}]


def test_merge_season_averages_sources_and_matches_dst_on_team():
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER)
    assert out[1]["season_proj"] == 370.0
    assert out[2]["season_proj"] == 100.0, "a Sleeper miss keeps ESPN alone"
    assert out[3]["season_proj"] == 85.0, "ESPN WSH D/ST matches Sleeper WAS on team"
    assert out[4]["season_proj"] == 150.0, "an ESPN 0 is a placeholder, not a projection"
    assert "season_proj_sleeper" not in out[1]


def test_merge_season_takes_a_dataframe_too():
    out = espn_season.merge_season(SEASON_ESPN, pd.DataFrame(SEASON_SLEEPER))
    assert out[1]["season_proj"] == 370.0 and out[3]["season_proj"] == 85.0


def test_merge_season_builds_ros_from_every_source():
    ros = {"sleeper": {("jalen hurts", "QB"): 300.0, ("WAS", "D/ST"): 70.0},
           "cbs": {("jalen hurts", "QB"): 280.0, ("zero guy", "TE"): 90.0},
           "sharks": {("jalen hurts", "QB"): 330.0}}
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER, ros)
    # Walsh averages of 280, 300, 330: 280, 290, 300, 305, 315, 330 -> (300 + 305) / 2
    assert out[1]["ros_proj"] == 302.5 and out[1]["ros_spread"] == 50.0 and out[1]["ros_n"] == 3
    assert out[1]["ros_src"] == {"sleeper": 300.0, "cbs": 280.0, "sharks": 330.0}
    assert out[3]["ros_proj"] == 70.0 and out[3]["ros_spread"] is None, "Sleeper alone, on team"
    assert out[4]["ros_proj"] == 90.0 and out[4]["ros_n"] == 1, "CBS alone"
    assert out[2]["ros_proj"] is None and out[2]["ros_n"] == 0, "none: season.ros_proj falls back"
    assert out[1]["season_proj"] == 370.0, "the season average is untouched"


def test_merge_season_two_ros_sources_is_the_old_mean():
    ros = {"sleeper": {("jalen hurts", "QB"): 300.0}, "cbs": {("jalen hurts", "QB"): 280.0}}
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER, ros)
    assert out[1]["ros_proj"] == 290.0 and out[1]["ros_spread"] == 20.0


def test_merge_season_without_ros_sources_leaves_ros_empty():
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER)
    assert out[1]["ros_proj"] is None and out[1]["ros_n"] == 0


def test_sleeper_ros_sums_the_weeks_a_player_has_a_row_for():
    weeks = [{("a", "RB"): 10.0, ("b", "WR"): 5.0}, {("a", "RB"): 12.0}, {("a", "RB"): 8.0, ("b", "WR"): 6.5}]
    assert espn_season.sleeper_ros(weeks) == {("a", "RB"): 30.0, ("b", "WR"): 11.5}, "a bye week is simply absent"
    assert espn_season.sleeper_ros([]) == {}


def test_apply_season_overrides_only_the_ids_it_knows():
    players = [espn_season.flatten_box_player(bp(playerId=1)), espn_season.flatten_box_player(bp(playerId=99))]
    out = espn_season.apply_season(players, {1: {"season_proj": 370.0, "ros_proj": 290.0, "ros_spread": 20.0,
                                                 "ros_n": 2, "ros_src": {"sleeper": 300.0, "cbs": 280.0}}})
    assert out[0]["season_proj"] == 370.0 and out[0]["ros_proj"] == 290.0 and out[0]["ros_n"] == 2
    assert "ros_src" not in out[0], "per-source numbers never reach a payload"
    assert out[1]["ros_proj"] is None, "an unknown id carries no ROS: the season formula prices him"
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
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.week_state(week=1, sources={}, season={}, injuries={}, ref=home_league())
    assert st["week"] == 1 and st["opp"] is None
    assert "stale" not in st, "the freshness flag is stale_since, added by the API's cache"
    assert st["me"]["team"] == "Mine" and len(st["me"]["lineup"]) == 11


def test_week_state_carries_the_leagues_starter_slots(monkeypatch):
    """The board pairs the sides slot by slot off this list, so an empty slot stays a blank row."""
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in season_roster()],
                              away_team=SimpleNamespace(), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.week_state(week=1, sources={}, season={}, injuries={}, ref=home_league())
    assert st["slots"] == season.starter_slots(TWO_QB)


def test_roster_state_builds_cards_for_my_side_only(monkeypatch):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in season_roster()],
                              away_team=SimpleNamespace(team_id=2, team_name="Them"),
                              away_lineup=[as_box(season_roster()[0])])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    assert st["week"] == 1 and isinstance(st["live"], bool)
    assert len(st["cards"]) == 15 and {c["group"] for c in st["cards"]} == {"starter", "bench", "ir"}
    assert all("call" in c and "reason" in c for c in st["cards"])


def test_roster_state_carries_locks_empty_slots_and_the_writes_flag(monkeypatch, caplog):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in season_roster() if p["name"] != "K"],
                              away_team=SimpleNamespace(team_id=2, team_name="Them"), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, **k: {1: {"locked": True}})
    st = espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    locked = {c["player_id"]: c["locked"] for c in st["cards"]}
    assert locked[1] is True and locked[2] is False
    assert st["empty_slots"] == ["K"] and st["writes"] is False, "the suite runs under FFL_WRITES=0"
    monkeypatch.setenv("FFL_WRITES", "1")
    assert espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())["writes"] is True
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, **k: 1 / 0)
    st = espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    assert not any(c["locked"] for c in st["cards"]), "a failed flags read shows every row unlocked"
    assert "roster_flags read failed" in caplog.text, "and says so in the log"


def test_roster_state_takes_slots_from_the_fresh_read_not_the_box_score(monkeypatch):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in season_roster()],
                              away_team=SimpleNamespace(team_id=2, team_name="Them"), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    bench = next(p for p in matchup.home_lineup if p.slot_position == "BE")
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, **k: {bench.playerId: {"locked": False, "slot": "RB/WR/TE"}})
    kw = dict(week=1, sources={}, season={}, injuries={}, snaps={})
    card = next(c for c in espn_season.roster_state(**kw, ref=home_league())["cards"] if c["player_id"] == bench.playerId)
    assert card["slot"] == "RB/WR/TE" and card["group"] == "starter", "the box score is a cache; ESPN's read is the page"
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, **k: 1 / 0)
    card = next(c for c in espn_season.roster_state(**kw, ref=home_league())["cards"] if c["player_id"] == bench.playerId)
    assert card["slot"] == "BE" and card["group"] == "bench", "a failed flags read keeps the box slot"


class FakeLeagueFactory:
    """Stands in for config.league: an lru_cache'd callable with a cache_clear()."""

    def __init__(self):
        self.builds = 0
        self.cleared = 0
        self.cached = None

    def __call__(self, *a, **k):
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
    assert espn_season.fresh_league(ref=home_league()).current_week == 1
    now[0] += espn_season.LEAGUE_TTL_S - 1
    assert espn_season.fresh_league(ref=home_league()).current_week == 1
    assert fake.builds == 1 and fake.cleared == 1, "one build, one clear on the very first call"


def test_fresh_league_rebuilds_after_the_ttl(monkeypatch):
    """espn_api reads current_week in League.__init__, so a launchd process that lives for
    months would sit on the boot week forever without this."""
    fake = FakeLeagueFactory()
    monkeypatch.setattr(espn_season, "league", fake)
    monkeypatch.setattr(espn_season, "_LEAGUE_BUILT_AT", 0.0)
    now = [1_000_000.0]
    monkeypatch.setattr(espn_season.time, "time", lambda: now[0])
    assert espn_season.fresh_league(ref=home_league()).current_week == 1
    now[0] += espn_season.LEAGUE_TTL_S + 1
    assert espn_season.fresh_league(ref=home_league()).current_week == 2
    assert fake.builds == 2


def test_refresh_league_forces_the_next_build(monkeypatch):
    fake = FakeLeagueFactory()
    monkeypatch.setattr(espn_season, "league", fake)
    monkeypatch.setattr(espn_season, "_LEAGUE_BUILT_AT", 0.0)
    espn_season.fresh_league(ref=home_league())
    espn_season.refresh_league()
    assert espn_season.fresh_league(ref=home_league()).current_week == 2, "POST /refresh must not wait out the TTL"


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


def test_parse_injuries_survives_a_return_date_that_is_not_a_date():
    """One bad row costs its own return date; the whole /week build must not fail on it."""
    row = {"shortComment": "Out indefinitely.", "details": {"returnDate": "TBD", "type": "Knee"},
           "athlete": {"links": [{"href": "https://www.espn.com/nfl/player/_/id/42/x"}]}}
    inj = espn_season.parse_injuries({"injuries": [{"displayName": "T", "injuries": [row]}]})
    assert inj[42] == {"return_date": None, "note": "Out indefinitely.", "body_part": "Knee",
                       "team": None, "pos": None, "status": None, "name": None}


def test_parse_injuries_tolerates_null_lists():
    """ESPN sends `"injuries": null` on a quiet day; a TypeError out of here breaks the whole build."""
    assert espn_season.parse_injuries({"injuries": None}) == {}
    assert espn_season.parse_injuries({"injuries": [{"displayName": "T", "injuries": None}]}) == {}


def test_parse_injuries_keeps_the_first_row_for_a_player():
    """A team's rows are newest-first, so the first row for an id is the current one."""
    link = {"href": "https://www.espn.com/nfl/player/_/id/42/x"}
    rows = [{"shortComment": "Now.", "details": {"returnDate": "2026-10-11"},
             "athlete": {"links": [link]}},
            {"shortComment": "Older.", "details": {"returnDate": "2026-09-13"},
             "athlete": {"links": [link]}}]
    inj = espn_season.parse_injuries({"injuries": [{"displayName": "T", "injuries": rows}]})
    assert inj[42]["return_date"] == date(2026, 10, 11) and inj[42]["note"] == "Now."


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


# --- the league's strategy, "Usage-adjusted ROS" (`uv run python -m ffl.strategy show`): who is hurt ahead of him, and when the week is over ------

def _inj_row(pid, name, pos, status, ret=None):
    return {"status": status, "shortComment": f"{name} note.",
            "details": {"returnDate": ret} if ret else None,
            "athlete": {"displayName": name, "position": {"abbreviation": pos},
                        "links": [{"href": f"https://www.espn.com/nfl/player/_/id/{pid}/x"}]}}


DEN = {"injuries": [{"id": "7", "displayName": "Denver Broncos", "injuries": [  # the live feed's id is a string
    _inj_row(101, "RJ Harvey", "RB", "Out", "2026-09-27"),
    _inj_row(102, "J.K. Dobbins", "RB", "Questionable", "2026-09-27"),
    _inj_row(103, "Jonah Coleman", "RB", "Active"),
    _inj_row(104, "Courtland Sutton", "WR", "Out", "2026-10-04")]}]}


def test_parse_injuries_carries_team_position_status_and_name():
    inj = espn_season.parse_injuries(DEN)
    assert inj[101]["team"] == "DEN" and inj[101]["pos"] == "RB" and inj[101]["status"] == "Out"
    assert inj[101]["name"] == "RJ Harvey" and inj[103]["status"] == "Active"
    old = espn_season.parse_injuries(INJ_FIXTURE)[CARTER]
    assert old["pos"] is None and old["name"], "a row without a position still parses"


def test_apply_injuries_lists_the_hurt_teammates_at_his_position_ahead_of_him():
    players = [espn_season.flatten_box_player(bp(playerId=103, name="Jonah Coleman", proTeam="DEN")),
               espn_season.flatten_box_player(bp(playerId=101, name="RJ Harvey", proTeam="DEN")),
               espn_season.flatten_box_player(bp(playerId=99, name="Someone Else", proTeam="KC"))]
    out = espn_season.apply_injuries(players, espn_season.parse_injuries(DEN), WEEK1)
    assert out[0]["ahead"] == [{"name": "RJ Harvey", "status": "OUT", "return_week": 3},
                               {"name": "J.K. Dobbins", "status": "QUESTIONABLE", "return_week": 3}]
    assert out[1]["ahead"] == [{"name": "J.K. Dobbins", "status": "QUESTIONABLE", "return_week": 3}], "never himself"
    assert out[2]["ahead"] == [], "a club with nobody on the report"
    assert "ahead" not in players[0] or players[0]["ahead"] == [], "pure"


def test_parse_kickoffs_by_week_reads_every_game_once_as_an_aware_datetime():
    data = {"settings": {"proTeams": [
        {"id": 7, "proGamesByScoringPeriod": {"2": [{"id": 1, "date": 1790554800000, "homeProTeamId": 7, "awayProTeamId": 14}]}},
        {"id": 14, "proGamesByScoringPeriod": {"2": [{"id": 1, "date": 1790554800000, "homeProTeamId": 7, "awayProTeamId": 14}]}},
        {"id": 0, "proGamesByScoringPeriod": {}}]}}
    kicks = espn_season.parse_kickoffs_by_week(data)
    assert list(kicks) == [2] and len(kicks[2]) == 1, "both clubs list the same game; it counts once"
    assert kicks[2][0].tzinfo is not None and kicks[2][0].timestamp() == 1790554800.0


def test_ros_week_for_rolls_off_the_schedule_and_leaves_espns_week_without_one(monkeypatch):
    lg = SimpleNamespace()  # no espn_request: the schedule document is empty
    assert espn_season.ros_week_for(lg, 2) == 2
    monkeypatch.setattr(espn_season, "kickoffs_by_week", lambda lg: {2: [datetime(2020, 1, 1, tzinfo=timezone.utc)]})
    assert espn_season.ros_week_for(lg, 2) == 3, "every kickoff long past"


def test_week_state_attaches_injuries_to_both_sides(monkeypatch):
    mine, theirs = season_roster(), season_roster()
    mine[0]["player_id"] = CARTER
    theirs[0]["player_id"] = LOVE
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in mine],
                              away_team=SimpleNamespace(team_id=8, team_name="Theirs"),
                              away_lineup=[as_box(p) for p in theirs])
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: SimpleNamespace(box_scores=lambda week: [matchup], current_week=1))
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: {**TWO_QB, "week1_start": WEEK1.isoformat()})
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.week_state(week=1, sources={}, season={},
                               injuries=espn_season.parse_injuries(INJ_FIXTURE), ref=home_league())
    hurt = next(p for p in st["me"]["lineup"] if p["player_id"] == CARTER)
    assert hurt["return_week"] == 5 and hurt["injury_note"]
    theirs_hurt = next(p for p in st["opp"]["lineup"] if p["player_id"] == LOVE)
    assert theirs_hurt["return_week"] == 1, "the opponent side is enriched too: 2026-09-13 is week 1"
    assert theirs_hurt["injury_note"].startswith("Coach Mike LaFleur")


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


PRO_SCHEDULE = {"settings": {"proTeams": [
    {"id": 0, "proGamesByScoringPeriod": {}},
    {"id": 25, "proGamesByScoringPeriod": {"1": [{"homeProTeamId": 14, "awayProTeamId": 25}]}},   # SF at LAR
    {"id": 14, "proGamesByScoringPeriod": {"1": [{"homeProTeamId": 14, "awayProTeamId": 25}]}},
    {"id": 8, "proGamesByScoringPeriod": {"2": [{"homeProTeamId": 8, "awayProTeamId": 3}]}},      # DET idle in week 1
]}}


def test_season_lookup_sums_the_remaining_sleeper_weeks_and_drops_them_on_a_failed_week(monkeypatch):
    espn_season._SEASON_CACHE.clear()
    espn_season._SL.clear()
    fetched = []

    def fake_week(week):
        fetched.append(week)
        if week == 17:
            raise requests.ConnectionError("sleeper down")
        return []

    monkeypatch.setattr(espn_season, "fetch_sleeper_rows", fake_week)
    monkeypatch.setattr(espn_season, "parse_sleeper_rows", lambda rows, ks: {("jalen hurts", "QB"): 10.0})
    monkeypatch.setattr(espn_season, "fetch_pool", lambda *, ref: SEASON_ESPN)
    monkeypatch.setattr(espn_season, "fetch_sleeper_season", lambda settings: SEASON_SLEEPER)
    monkeypatch.setattr(espn_season, "ROS_SOURCES", {"cbs": lambda ks, week: {("jalen hurts", "QB"): 150.0}})
    settings = {**TWO_QB, "scoring": []}
    out = espn_season.season_lookup(settings, week=16, ref=home_league())
    assert sorted(fetched) == [16, 17, 18]
    assert out[1]["ros_src"] == {"cbs": 150.0} and out[1]["ros_proj"] == 150.0, \
        "one missing week would undercount every player: Sleeper's ROS is dropped, CBS carries it"
    espn_season._SEASON_CACHE.clear()
    espn_season._SL.clear()
    fetched.clear()
    out = espn_season.season_lookup(settings, week=18, ref=home_league())
    assert fetched == [18] and out[1]["ros_src"]["sleeper"] == 10.0 and out[1]["ros_proj"] == 80.0
    assert espn_season.season_lookup(settings, week=18, ref=home_league()) is out, "cached per week"
    assert espn_season.season_lookup(settings, week=17, ref=home_league()) is not out, "another week is another sum"


def test_season_lookup_keeps_every_week_so_a_past_board_never_evicts_the_current_one(monkeypatch):
    espn_season._SEASON_CACHE.clear()
    builds = []
    monkeypatch.setattr(espn_season, "fetch_pool", lambda *, ref: builds.append(1) or SEASON_ESPN)
    monkeypatch.setattr(espn_season, "fetch_sleeper_season", lambda settings: SEASON_SLEEPER)
    monkeypatch.setattr(espn_season, "_remaining_sleeper_weeks", lambda week, settings: None)
    monkeypatch.setattr(espn_season, "ROS_SOURCES", {})
    settings = {**TWO_QB, "scoring": []}
    current = espn_season.season_lookup(settings, week=7, ref=home_league())
    espn_season.season_lookup(settings, week=6, ref=home_league())  # the Matchup board's scoring week, or a past week's board
    espn_season.season_lookup(settings, week=2, ref=home_league())
    assert espn_season.season_lookup(settings, week=7, ref=home_league()) is current and len(builds) == 3
    for w in range(8, 19):
        espn_season.season_lookup(settings, week=w, ref=home_league())
    assert espn_season.season_lookup(settings, week=2, ref=home_league()) and len(builds) == 14, "nothing is dropped to make room"


def test_free_agent_pool_reports_whether_ros_came_from_the_sources(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    out = espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    assert out["ros_source"] == "season"
    out = espn_season.free_agent_pool(week=1, sources={}, season={1001: {"season_proj": 1.0, "ros_proj": 5.0,
                                                                          "ros_n": 1, "ros_spread": None}},
                                      injuries={}, snaps={}, ref=home_league())
    assert out["ros_source"] == "sources"


def test_parse_home_by_team_reads_the_venue_espn_api_drops():
    home = espn_season.parse_home_by_team(PRO_SCHEDULE, 1)
    assert home == {"SF": False, "LAR": True}, "DET has no week-1 game and team 0 is the placeholder"


def test_apply_home_leaves_players_without_a_game_unknown():
    players = [espn_season.flatten_box_player(bp(proTeam="SF", pro_opponent="LAR")),
               espn_season.flatten_box_player(bp(playerId=2, proTeam="LAR", pro_opponent="SF")),
               espn_season.flatten_box_player(bp(playerId=3, proTeam="DET", on_bye_week=True, pro_opponent="None")),
               espn_season.flatten_box_player(bp(playerId=4, proTeam="MIA", pro_opponent="NYJ"))]
    espn_season.apply_home(players, {"SF": False, "LAR": True})
    assert [p["home"] for p in players] == [False, True, None, None], "a bye and an unlisted team both stay None"


def test_home_by_team_survives_a_failed_schedule_fetch(monkeypatch, capsys):
    espn_season._HOME.clear()

    def boom():
        raise requests.ConnectionError("down")
    lg = SimpleNamespace(espn_request=SimpleNamespace(get_pro_schedule=boom))
    assert espn_season.home_by_team(lg, 1) == {}
    assert "venues unknown" in capsys.readouterr().out
    assert not espn_season._HOME, "a failure is not cached: the next request retries"
    lg = SimpleNamespace(espn_request=SimpleNamespace(get_pro_schedule=lambda: PRO_SCHEDULE))
    assert espn_season.home_by_team(lg, 1) == {"SF": False, "LAR": True}
    lg.espn_request.get_pro_schedule = boom
    assert espn_season.home_by_team(lg, 1) == {"SF": False, "LAR": True}, "cached for the League's lifetime"


def _stub_league(matchup, calls):
    def box_scores(week):
        calls["box"] += 1
        return [matchup]

    def free_agents(week, size, position):
        calls["fa"].append(position)
        return [as_box({**season_roster()[0], "player_id": 1000 + len(calls["fa"]), "slot": "FA"})]
    return SimpleNamespace(box_scores=box_scores, free_agents=free_agents, current_week=1)


def _matchup():
    return SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                           home_lineup=[as_box(p) for p in season_roster()],
                           away_team=SimpleNamespace(team_id=2, team_name="Them"),
                           away_lineup=[as_box(season_roster()[0])])


def test_free_agent_pool_carries_every_rostered_player_with_owner_and_worth_to_me(monkeypatch):
    calls = {"box": 0, "fa": []}
    m = _matchup()
    m.away_team = SimpleNamespace(team_id=2, team_name="Them", team_abbrev="THM")
    m.away_lineup = [as_box({**season_roster()[3], "player_id": 500, "name": "Their RB"})]
    lg = _stub_league(m, calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    pool = espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    rb = {r["player_id"]: r for r in pool["rostered"]["RB"]}
    them = rb[500]
    assert them["owner"] == {"team_id": 2, "name": "Them", "abbrev": "THM"} and them["mine"] is False
    assert isinstance(them["add"], float) and isinstance(them["delta_roster"], float), "worth to me, as a free agent's"
    mine = rb[4]  # season_roster()[3], RB A
    assert mine["mine"] is True and mine["owner"]["team_id"] == 7
    assert mine["owner"]["abbrev"] is None, "the stub team has no team_abbrev: None, not ''"
    assert all(mine[k] is None for k in ("add", "delta_roster", "delta_pool", "week_delta", "would_start"))
    frees = [r for rows in pool["positions"].values() for r in rows]
    assert all(r["owner"] is None and r["mine"] is False for r in frees)
    assert 500 not in {r["player_id"] for r in frees}, "positions stays free agents only"
    assert calls["box"] == 1, "owners come off the same box_scores fetch"


def test_builders_share_one_box_scores_fetch(monkeypatch):
    """Every page rebuilds within the same minute or two; the 0.7 s box_scores call is made once."""
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    espn_season.week_state(week=1, sources={}, season={}, injuries={}, ref=home_league())
    espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    assert calls["box"] == 1
    other = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: other)
    espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    assert calls["box"] == 2, "a rebuilt League is a new source: its box scores are fetched again"


def test_free_agent_pool_fetches_every_position_in_position_order(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    out = espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())
    assert list(out["positions"]) == ["QB", "RB", "WR", "TE", "D/ST", "K"]
    assert sorted(calls["fa"]) == sorted(["QB", "RB", "WR", "TE", "D/ST", "K"]), "each fetched exactly once"
    assert all(row["slot"] == "FA" for rows in out["positions"].values() for row in rows)


def test_season_lookup_fetches_its_four_sources_together(monkeypatch):
    """ESPN's pool, Sleeper's season, Sleeper's weeks and CBS's pages are four independent fetches
    of one to four seconds each; one after another they were most of the Refresh button's wait.
    Each fake waits on a barrier for the other three: run in sequence, the barrier times out."""
    import threading
    espn_season._SEASON_CACHE.clear()
    gate = threading.Barrier(4, timeout=2)

    def together(value):
        def fn(*a, **k):
            gate.wait()
            return value
        return fn

    monkeypatch.setattr(espn_season, "fetch_pool", together(SEASON_ESPN))
    monkeypatch.setattr(espn_season, "fetch_sleeper_season", together(SEASON_SLEEPER))
    monkeypatch.setattr(espn_season, "_remaining_sleeper_weeks", together([{("jalen hurts", "QB"): 10.0}]))
    monkeypatch.setattr(espn_season, "ROS_SOURCES", {"cbs": together({("jalen hurts", "QB"): 150.0})})
    out = espn_season.season_lookup({**TWO_QB, "scoring": []}, week=18, ref=home_league())
    assert out[1]["ros_src"] == {"sleeper": 10.0, "cbs": 150.0}
    espn_season._SEASON_CACHE.clear()


def test_stat_row_maps_espn_names_back_to_the_scored_ids():
    settings = {"scoring": [{"stat_id": 3, "points": 0.04}, {"stat_id": 53, "points": 1.0}]}
    row = espn_season.stat_row(bp(breakdown={"passingYards": 250, "receivingReceptions": 4, "214": 2}), settings)
    assert row == {"3": 250.0, "53": 4.0, "214": 2.0}
    assert espn_season.stat_row(bp(breakdown={}), settings) is None
    assert espn_season.stat_row(bp(), settings) is None


def _two_games(*a, **k):
    """Week 1 with two matchups: mine (7 at home against 2) and 3 at 4, away first."""
    roster = [as_box(p) for p in season_roster()]
    mine = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"), home_lineup=roster,
                           away_team=SimpleNamespace(team_id=2, team_name="Them"), away_lineup=roster[:3])
    other = SimpleNamespace(home_team=SimpleNamespace(team_id=4, team_name="Home"), home_lineup=roster[:2],
                            away_team=SimpleNamespace(team_id=3, team_name="Away"), away_lineup=roster[:4])
    return SimpleNamespace(box_scores=lambda week: [mine, other], current_week=1)


def test_week_state_for_another_team_puts_the_away_side_first(monkeypatch):
    """Any matchup on the League scoreboard opens on the Matchup board: the away team on the
    left, as the scoreboard tile stacks it, whichever of the two was asked for."""
    monkeypatch.setattr(espn_season, "league", _two_games)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    for team in (3, 4):
        st = espn_season.week_state(week=1, sources={}, season={}, injuries={}, team=team, ref=home_league())
        assert (st["me"]["team"], st["opp"]["team"], st["mine"]) == ("Away", "Home", False)


def test_week_state_keeps_me_on_the_left_of_my_own_game(monkeypatch):
    monkeypatch.setattr(espn_season, "league", _two_games)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    for team in (None, 7, 2):
        st = espn_season.week_state(week=1, sources={}, season={}, injuries={}, team=team, ref=home_league())
        assert (st["me"]["team"], st["opp"]["team"], st["mine"]) == ("Mine", "Them", True)


def test_week_state_for_an_unknown_team_raises(monkeypatch):
    monkeypatch.setattr(espn_season, "league", _two_games)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    with pytest.raises(LookupError):
        espn_season.week_state(week=1, sources={}, season={}, injuries={}, team=99, ref=home_league())


def test_team_state_lists_any_team_with_its_club_and_rank(monkeypatch):
    """The team page: one side of the week's box scores, any team's, with the club header."""
    theirs = season_roster()
    club = dict(team_abbrev="THM", logo_url="", wins=2, losses=1, ties=0)
    them = SimpleNamespace(team_id=8, team_name="Theirs", **club)
    mine = SimpleNamespace(team_id=7, team_name="Mine", **club)
    matchup = SimpleNamespace(home_team=mine, home_lineup=[as_box(p) for p in season_roster()],
                              away_team=them, away_lineup=[as_box(p) for p in theirs])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1, teams=[mine, them],
                         standings=lambda: [them, mine])
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.team_state(8, week=1, sources={}, season={}, injuries={}, ref=home_league())
    assert st["week"] == 1 and st["mine"] is False
    assert st["club"] == {"team_id": 8, "team": "Theirs", "owner": "", "abbrev": "THM", "logo": "", "wins": 2,
                          "losses": 1, "ties": 0, "rank": 1}
    side = st["side"]
    assert side["team_id"] == 8 and len(side["lineup"]) == 11
    assert len(side["lineup"]) + len(side["bench"]) + len(side["ir"]) == len(theirs)
    assert espn_season.team_state(7, week=1, sources={}, season={}, injuries={}, ref=home_league())["mine"] is True
    with pytest.raises(LookupError):
        espn_season.team_state(99, week=1, sources={}, season={}, injuries={}, ref=home_league())


def test_team_state_lists_a_team_without_a_game_from_its_roster(monkeypatch):
    """A playoff bye: no box score, so the roster's lineupSlot places each player."""
    qb, rb = season_roster()[0], season_roster()[2]
    roster = [SimpleNamespace(**vars(as_box(qb)), lineupSlot="QB"), SimpleNamespace(**vars(as_box(rb)), lineupSlot="BE")]
    bye = SimpleNamespace(team_id=9, team_name="Idle", team_abbrev="IDL", logo_url="", wins=9, losses=5, ties=0,
                          roster=roster)
    lg = SimpleNamespace(box_scores=lambda week: [], current_week=15, teams=[bye], standings=lambda: [bye])
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    side = espn_season.team_state(9, week=15, sources={}, season={}, injuries={}, ref=home_league())["side"]
    assert [p["player_id"] for p in side["lineup"]] == [qb["player_id"]]
    assert [p["player_id"] for p in side["bench"]] == [rb["player_id"]]


def _fallback_sigma(monkeypatch):
    from ffl import season
    monkeypatch.setattr(espn_season.calibrate, "load_sigma", lambda: (season.SIGMA_FALLBACK, "fallback"))


def test_week_state_carries_the_odds_of_its_game(monkeypatch):
    monkeypatch.setattr(espn_season, "league", _two_games)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    _fallback_sigma(monkeypatch)
    odds = espn_season.week_state(week=1, sources={}, season={}, injuries={}, ref=home_league())["win_prob"]
    assert odds["sigma_source"] == "fallback"
    assert odds["me"] > 0.9, "a full lineup against three players"
    assert abs(odds["me"] + odds["opp"] - 1) < 0.002


def _finished_week(monkeypatch, rows):
    """Week 1 of a league now in week 3: the board of a finished week, its kickoff projections in `rows`."""
    def league(*a, **k):
        lg = _two_games()
        lg.current_week = 3
        return lg
    monkeypatch.setattr(espn_season, "league", league)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season.db, "projection_rows", lambda kind, week=None, **k: [
        r for r in rows if r["week"] == week and r.get("kind", "week") == kind])
    saved = []
    monkeypatch.setattr(espn_season.db, "put_projection_rows",
                        lambda week, kind, rs, **k: saved.append((week, kind, rs)) or len(rs))
    monkeypatch.setattr(espn_season, "_SAVED_LATE", saved, raising=False)
    looked = []
    monkeypatch.setattr(espn_season, "season_lookup", lambda settings, week, **k: looked.append(week) or {})
    _fallback_sigma(monkeypatch)
    return looked


def test_a_finished_weeks_board_reads_the_projections_frozen_at_its_kickoffs(monkeypatch):
    """Not a refetch of every source (9-15 s cold, and a source may rewrite a week once it is played)."""
    qb = season_roster()[0]
    looked = _finished_week(monkeypatch, [
        {"week": 1, "player_id": qb["player_id"], "source": "sleeper", "points": 30.0},
        {"week": 1, "player_id": qb["player_id"], "source": "cbs", "points": 26.0},
        {"week": 2, "player_id": qb["player_id"], "source": "cbs", "points": 1.0}])
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: pytest.fail("the week is recorded"))
    st = espn_season.week_state(week=1, injuries={}, ref=home_league())
    me = next(p for p in st["me"]["lineup"] if p["player_id"] == qb["player_id"])
    assert me["proj_src"]["sleeper"] == 30.0 and me["proj_src"]["cbs"] == 26.0
    assert looked == [3], "ROS from today's lookup (the warm one), not a 5-25 s build of week 1's"


def test_a_finished_week_without_recorded_projections_fetches_them_once_and_keeps_them(monkeypatch):
    """Recording began in week 5: an earlier week asks the sources the first time, and what they said is saved
    under its own kind (fetched after the games, so never mistaken for a kickoff number by calibration)."""
    _finished_week(monkeypatch, [])
    qb = season_roster()[0]
    key = espn_season.source_key(espn_season.flatten_box_player(as_box(qb)))
    fetched = []
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: fetched.append(week) or {
        "sleeper": {key: 24.0}, "sharks": {key: 22.0}, "cbs": {key: 99.0}, "fleaflicker": {}})
    st = espn_season.week_state(week=1, injuries={}, ref=home_league())
    assert fetched == [1]
    (week, kind, rows), = espn_season._SAVED_LATE
    assert (week, kind) == (1, espn_season.LATE)
    assert {"player_id": qb["player_id"], "source": "sleeper", "points": 24.0} in rows
    assert not any(r["source"] == "cbs" for r in rows), "CBS's week page is the current week's, whatever week"
    assert "cbs" not in next(p for p in st["me"]["lineup"] if p["player_id"] == qb["player_id"])["proj_src"]


def test_a_finished_week_is_not_kept_while_a_source_is_down(monkeypatch):
    _finished_week(monkeypatch, [])
    key = espn_season.source_key(espn_season.flatten_box_player(as_box(season_roster()[0])))
    monkeypatch.setattr(espn_season, "weekly_sources",
                        lambda week, settings: {"sleeper": {key: 24.0}, "sharks": {}})
    espn_season.week_state(week=1, injuries={}, ref=home_league())
    assert espn_season._SAVED_LATE == [], "asked again next time, not frozen without FantasySharks"


def test_a_finished_week_is_kept_without_a_source_switched_off(monkeypatch):
    """FFL_SOURCES_OFF=sharks (the box): Sleeper alone is the whole set, so the week freezes rather than refetching forever."""
    monkeypatch.setenv("FFL_SOURCES_OFF", "sharks")
    _finished_week(monkeypatch, [])
    key = espn_season.source_key(espn_season.flatten_box_player(as_box(season_roster()[0])))
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: {"sleeper": {key: 24.0}})
    espn_season.week_state(week=1, injuries={}, ref=home_league())
    (_, kind, rows), = espn_season._SAVED_LATE
    assert kind == espn_season.LATE and {r["source"] for r in rows} - {"espn"} == {"sleeper"}


def test_sources_off_takes_known_names_only_and_warns_once_about_the_rest(monkeypatch, caplog):
    from ffl import config
    monkeypatch.setattr(config, "_WARNED", set())
    monkeypatch.setenv("FFL_SOURCES_OFF", " sharks , sleeper,,cbs-typo")
    with caplog.at_level("WARNING", logger="ffl.config"):
        assert config.sources_off() == {"sharks"}
        assert config.source_off("sharks") and not config.source_off("sleeper"), "sleeper has no switch: never off"
        assert not config.source_off("youtube_captions")
    warned = [r.getMessage() for r in caplog.records if r.name == "ffl.config"]
    assert warned == ["FFL_SOURCES_OFF: ignoring unknown cbs-typo, sleeper (known: sharks, youtube_captions)"]
    monkeypatch.delenv("FFL_SOURCES_OFF")
    assert config.sources_off() == frozenset()


def test_a_switched_off_source_is_absent_never_asked_and_never_failing(monkeypatch):
    from ffl import sources
    sources.reset()
    sources.STATUS.pop("sharks", None)
    monkeypatch.setenv("FFL_SOURCES_OFF", "sharks,youtube_captions")
    monkeypatch.setattr(espn_season, "_sleeper_for", lambda week, settings: {("a", "RB"): 3.0})
    monkeypatch.setattr(espn_season, "WEEKLY_SOURCES", {"cbs": lambda w, ks: {("a", "RB"): 1.0},
                                                         "sharks": lambda w, ks: pytest.fail("sharks asked")})
    out = espn_season.weekly_sources(5, {"scoring": []})
    assert out == {"sleeper": {("a", "RB"): 3.0}, "cbs": {("a", "RB"): 1.0}}
    p = {"player_id": 1, "name": "A", "pos": "RB", "team": "DET", "proj_espn": 0}
    assert espn_season.merge_weekly([p], out)[0]["n_src"] == 2, "the consensus runs on the rest"
    assert "sharks" not in sources.status_snapshot(), "no failure recorded, so no 24 h notice"

    monkeypatch.setattr(espn_season, "fetch_pool", lambda *, ref: SEASON_ESPN)
    monkeypatch.setattr(espn_season, "fetch_sleeper_season", lambda settings: SEASON_SLEEPER)
    monkeypatch.setattr(espn_season, "_remaining_sleeper_weeks", lambda week, settings: None)
    monkeypatch.setattr(espn_season, "ROS_SOURCES", {"cbs": lambda ks, week: {("jalen hurts", "QB"): 150.0},
                                                     "sharks": lambda ks, week: pytest.fail("sharks ROS asked")})
    espn_season._SEASON_CACHE.clear()
    got = espn_season.season_lookup({**TWO_QB, "scoring": []}, week=16, ref=home_league())
    assert got[1]["ros_src"] == {"cbs": 150.0} and got[1]["ros_n"] == 1
    espn_season._SEASON_CACHE.clear()


def test_a_finished_week_reads_projections_saved_after_its_games(monkeypatch):
    qb = season_roster()[0]
    _finished_week(monkeypatch, [{"week": 1, "kind": espn_season.LATE, "player_id": qb["player_id"],
                                  "source": "cbs", "points": 24.0}])
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: pytest.fail("saved already"))
    st = espn_season.week_state(week=1, injuries={}, ref=home_league())
    assert next(p for p in st["me"]["lineup"] if p["player_id"] == qb["player_id"])["proj_src"]["cbs"] == 24.0
    assert espn_season._SAVED_LATE == []


def test_the_current_weeks_board_fetches_live(monkeypatch):
    looked = _finished_week(monkeypatch, [{"week": 3, "player_id": 1, "source": "cbs", "points": 1.0}])
    fetched = []
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: fetched.append(week) or {})
    espn_season.week_state(week=3, injuries={}, ref=home_league())
    assert fetched == [3] and looked == [3]


def test_league_state_survives_sleeper_down(monkeypatch):
    """The scoreboard did not need Sleeper before the odds; an outage falls back to ESPN's numbers."""
    club = dict(team_abbrev="X", logo_url="", wins=1, losses=0, ties=0, points_for=1.0, points_against=1.0)
    mine = SimpleNamespace(team_id=7, team_name="Mine", **club)
    them = SimpleNamespace(team_id=2, team_name="Them", **club)
    boxes = [as_box(p) for p in season_roster()]
    match = SimpleNamespace(home_team=mine, home_lineup=boxes, away_team=them, away_lineup=boxes[:3],
                            home_score=0.0, away_score=0.0, home_projected=0.0, away_projected=0.0)
    lg = SimpleNamespace(current_week=1, standings=lambda: [mine, them], box_scores=lambda wk: [match],
                         scoreboard=lambda wk: [])
    monkeypatch.setattr(espn_season, "fresh_league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: {**TWO_QB, "reg_season_weeks": 14, "playoff_teams": 6})

    def down(week, settings):
        raise requests.HTTPError("503")
    monkeypatch.setattr(espn_season, "_sleeper_for", down)
    _fallback_sigma(monkeypatch)
    row = espn_season.league_state(1, ref=home_league())["scoreboard"][0]
    qb_a = next(s for s in row["home"]["starters"] if s["player_id"] == 1)
    assert qb_a["proj"] == 20, "ESPN's own projection stands"
    assert row["win_prob"] is not None


def test_league_state_carries_starters_merged_proj_and_odds(monkeypatch):
    from ffl import season
    club = dict(team_abbrev="X", logo_url="", wins=1, losses=0, ties=0, points_for=1.0, points_against=1.0)
    mine = SimpleNamespace(team_id=7, team_name="Mine", **club)
    them = SimpleNamespace(team_id=2, team_name="Them", **club)
    boxes = [as_box(p) for p in season_roster()]
    match = SimpleNamespace(home_team=mine, home_lineup=boxes, away_team=them, away_lineup=boxes[:3],
                            home_score=0.0, away_score=0.0, home_projected=999.0, away_projected=999.0)
    lg = SimpleNamespace(current_week=1, standings=lambda: [mine, them], box_scores=lambda wk: [match],
                         scoreboard=lambda wk: [])
    monkeypatch.setattr(espn_season, "fresh_league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: {**TWO_QB, "scoring": [], "reg_season_weeks": 14, "playoff_teams": 6})
    monkeypatch.setattr(espn_season, "_sleeper_for", lambda week, settings: {("qb a", "QB"): 30.0})
    _fallback_sigma(monkeypatch)
    row = espn_season.league_state(1, ref=home_league())["scoreboard"][0]
    starters = [p for p in season_roster() if p["slot"] not in season.BENCH_SLOTS]
    assert {s["player_id"] for s in row["home"]["starters"]} == {p["player_id"] for p in starters}
    qb_a = next(s for s in row["home"]["starters"] if s["player_id"] == 1)
    assert qb_a["proj"] == 25.0, "the board's ESPN+Sleeper mean, not ESPN's alone"
    assert row["home"]["proj"] == round(sum(p["proj"] for p in starters) + 5.0, 1), "not ESPN's side projection"
    assert row["win_prob"]["home"] > 0.9 and abs(row["win_prob"]["home"] + row["win_prob"]["away"] - 1) < 0.002


def test_a_finished_weeks_scoreboard_reads_the_recorded_projections(monkeypatch):
    """Like the Matchup board: a past week on the League page asked every source live, 7-9 s cold (ffl-bdh)."""
    club = dict(team_abbrev="X", logo_url="", wins=1, losses=0, ties=0, points_for=1.0, points_against=1.0)
    mine = SimpleNamespace(team_id=7, team_name="Mine", **club)
    them = SimpleNamespace(team_id=2, team_name="Them", **club)
    boxes = [as_box(p) for p in season_roster()]
    match = SimpleNamespace(home_team=mine, home_lineup=boxes, away_team=them, away_lineup=boxes[:3],
                            home_score=0.0, away_score=0.0, home_projected=0.0, away_projected=0.0)
    lg = SimpleNamespace(current_week=3, standings=lambda: [mine, them], box_scores=lambda wk: [match],
                         scoreboard=lambda wk: [])
    monkeypatch.setattr(espn_season, "fresh_league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: {**TWO_QB, "reg_season_weeks": 14, "playoff_teams": 6})
    monkeypatch.setattr(espn_season.db, "projection_rows", lambda kind, week=None, **k: [
        {"week": 1, "player_id": 1, "source": "sleeper", "points": 30.0}] if (kind, week) == ("week", 1) else [])
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: pytest.fail("the week is recorded"))
    _fallback_sigma(monkeypatch)
    row = espn_season.league_state(1, ref=home_league())["scoreboard"][0]
    assert next(s for s in row["home"]["starters"] if s["player_id"] == 1)["proj"] == 25.0, "ESPN 20 and Sleeper 30"


FX = Path(__file__).parent / "fixtures" / "espn_claim_facts"


def test_parse_pool_status():
    doc = json.loads((FX / "pool_status.json").read_text())
    st = espn_season.parse_pool_status(doc)
    assert st and all(v["status"] in {"FREEAGENT", "WAIVERS", "ONTEAM"} for v in st.values())
    assert all(isinstance(k, int) for k in st)
    assert any(k < 0 for k in st), "a D/ST id is negative"


def test_parse_pool_status_handles_missing_date():
    st = espn_season.parse_pool_status({"players": [{"id": -16020, "status": "FREEAGENT"}]})
    assert st == {-16020: {"status": "FREEAGENT", "waiver_date": None}}


def test_parse_roster_flags():
    doc = json.loads((FX / "roster.json").read_text())
    team_id = doc["teams"][0]["id"]
    f = espn_season.parse_roster_flags(doc, team_id)
    assert f and all({"droppable", "locked"} <= set(v) for v in f.values())
    assert [v["droppable"] for v in f.values()].count(False) == 1, "Puka Nacua is the one undroppable player"


def test_parse_roster_flags_undroppable():
    doc = {"teams": [{"id": 7, "roster": {"entries": [
        {"playerId": 1, "playerPoolEntry": {"lineupLocked": True, "player": {"droppable": False}}}]}}]}
    assert espn_season.parse_roster_flags(doc, 7) == {1: {"droppable": False, "locked": True, "order": 0}}


def test_pool_status_asks_by_status(monkeypatch):
    seen = {}

    def fake(view, headers=None, **k):
        seen.update(view=view, flt=json.loads(headers["x-fantasy-filter"]))
        return {"players": []}
    monkeypatch.setattr(espn_season, "raw_league_get", fake)
    assert espn_season.pool_status(ref=home_league()) == {}
    assert seen["view"] == "kona_player_info" and "filterIds" not in seen["flt"]["players"]


def _pool_with_facts(monkeypatch, status, flags):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "pool_status", status)
    monkeypatch.setattr(espn_season, "roster_flags", flags)
    return espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())


def test_free_agent_pool_rows_carry_claim_facts(monkeypatch):
    first = season_roster()[0]["player_id"]
    out = _pool_with_facts(monkeypatch, lambda **k: {1001: {"status": "FREEAGENT", "waiver_date": 5}},
                           lambda lg, **k: {first: {"droppable": False, "locked": True}})
    rows = [r for rs in out["positions"].values() for r in rs]
    assert all(set(r) >= {"status", "waiver_date"} for r in rows)
    assert {r["status"] for r in rows} <= {"FREEAGENT", None}
    mine = {r["player_id"]: r for r in out["roster"]}
    assert mine[first]["droppable"] is False and mine[first]["locked"] is True
    assert all(set(r) >= {"droppable", "locked"} for r in out["roster"])


def test_free_agent_pool_rows_carry_defaults_when_the_reads_raise(monkeypatch):
    def boom(*a):
        raise requests.ConnectionError("espn down")
    out = _pool_with_facts(monkeypatch, boom, boom)
    rows = [r for rs in out["positions"].values() for r in rs]
    assert rows and all(r["status"] is None and r["waiver_date"] is None for r in rows), "unknown counts as WAIVERS"
    assert out["roster"] and all(r["droppable"] is True and r["locked"] is False for r in out["roster"])


def _deep_doc(pid: int, w: int) -> dict:
    """One RB as kona_player_info answers by id for scoringPeriodId w (as tests.test_form_io._info_doc)."""
    def row(source, total):
        return {"seasonId": espn_season.SEASON, "scoringPeriodId": w, "statSourceId": source, "statSplitTypeId": 1,
                "appliedTotal": total, "stats": {"23": 12}}
    return {"id": pid, "player": {"id": pid, "fullName": "Deep Back", "eligibleSlots": [2, 20], "proTeamId": 1,
                                  "defaultPositionId": 2, "injuryStatus": "ACTIVE",
                                  "stats": [row(0, 0.0), row(1, 9.5)]}}


def _watched_pool(monkeypatch, matchup, get):
    """free_agent_pool over the stub league, the claim-fact reads stubbed so `get` sees only the by-id ask."""
    calls = {"box": 0, "fa": []}
    lg = _stub_league(matchup, calls)
    monkeypatch.setattr(espn_season, "_schedule_doc", lambda lg: {"settings": {"proTeams": [{"id": 1, "proGamesByScoringPeriod": {
        "1": [{"homeProTeamId": 2, "awayProTeamId": 1, "date": 1_800_000_000_000}]}}]}})
    lg._get_positional_ratings = lambda w: {}
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "pool_status", lambda **k: {999: {"status": "WAIVERS", "waiver_date": 5}})
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, **k: {})
    monkeypatch.setattr(espn_season, "raw_league_get", get)
    return espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={}, ref=home_league())


def test_pool_watched_includes_deep_free_agent(monkeypatch):
    from ffl import db
    db.put_watch(999, True, ref=home_league())
    db.put_watch(1001, True, ref=home_league())  # the stub's first free agent: in the pool already, reused
    asked = []

    def get(view, headers=None, *, ref=None, **params):
        flt = json.loads(headers["x-fantasy-filter"])["players"]
        asked.append((view, flt, params))
        return {"players": [_deep_doc(pid, params["scoringPeriodId"]) for pid in flt["filterIds"]["value"]]}
    pool = _watched_pool(monkeypatch, _matchup(), get)
    assert asked == [("kona_player_info", {"filterIds": {"value": [999]}}, {"scoringPeriodId": 1})], \
        "one by-id ask for this week, only for the man the pool lacks, no limit"
    watched = {r["player_id"]: r for r in pool["watched"]}
    assert list(watched) == [999, 1001], "one row per watched id, in watch order"
    deep = watched[999]
    assert deep["pos"] == "RB" and deep["slot"] == "FA" and deep["owner"] is None and deep["mine"] is False
    assert isinstance(deep["add"], float) and isinstance(deep["delta_roster"], float)
    assert deep["week_proj"] == 9.5, "this week's ESPN projection off the by-id answer"
    assert deep["status"] == "WAIVERS" and deep["waiver_date"] == 5, "claim facts as a pool row's"
    assert 999 not in {r["player_id"] for rows in pool["positions"].values() for r in rows}
    assert 999 not in {r["player_id"] for rows in pool["rostered"].values() for r in rows}
    fa = next(r for rows in pool["positions"].values() for r in rows if r["player_id"] == 1001)
    assert watched[1001] == fa, "a pool row is reused, not repriced"


def test_pool_watched_rostered_elsewhere_has_owner(monkeypatch):
    from ffl import db
    m = _matchup()
    m.away_team = SimpleNamespace(team_id=2, team_name="Them", team_abbrev="THM")
    m.away_lineup = [as_box({**season_roster()[3], "player_id": 500, "name": "Their RB"})]
    db.put_watch(500, True, ref=home_league())
    pool = _watched_pool(monkeypatch, m, lambda *a, **k: pytest.fail("a rostered man is never fetched"))
    [row] = pool["watched"]
    assert row["player_id"] == 500 and row["mine"] is False
    assert row["owner"] == {"team_id": 2, "name": "Them", "abbrev": "THM"}
    them = next(r for r in pool["rostered"]["RB"] if r["player_id"] == 500)
    assert row["add"] == them["add"] and row["delta_roster"] == them["delta_roster"], "worth to me, as on the Players page"


def test_pool_watched_fetch_failure_omits_not_fails(monkeypatch):
    from ffl import db
    db.put_watch(999, True, ref=home_league())
    db.put_watch(-16001, True, ref=home_league())  # a D/ST no list carries: never asked by id

    def boom(*a, **k):
        raise requests.ConnectionError("espn down")
    pool = _watched_pool(monkeypatch, _matchup(), boom)
    assert pool["watched"] == []
    assert list(pool["positions"]) == ["QB", "RB", "WR", "TE", "D/ST", "K"] and pool["roster"]


def test_parse_roster_flags_carries_the_slot_name():
    doc = {"teams": [{"id": 7, "roster": {"entries": [
        {"playerId": 1, "lineupSlotId": 20, "playerPoolEntry": {}},
        {"playerId": 2, "lineupSlotId": 23, "playerPoolEntry": {}},
        {"playerId": 3, "lineupSlotId": 99, "playerPoolEntry": {}}]}}]}
    f = espn_season.parse_roster_flags(doc, 7)
    assert f[1]["slot"] == "BE" and f[2]["slot"] == "RB/WR/TE" and "slot" not in f[3]


def test_parse_roster_flags_carries_espns_order():
    doc = {"teams": [{"id": 7, "roster": {"entries": [
        {"playerId": 5, "playerPoolEntry": {}}, {"playerId": 2, "playerPoolEntry": {}}]}}]}
    f = espn_season.parse_roster_flags(doc, 7)
    assert f[5]["order"] == 0 and f[2]["order"] == 1


def test_with_espn_slots_carries_espns_order_and_falls_back_to_the_box_order():
    rows = [{"player_id": 1, "slot": "WR"}, {"player_id": 2, "slot": "WR"}, {"player_id": 3, "slot": "BE"}]
    out = espn_season.with_espn_slots(rows, {1: {"slot": "WR", "order": 4}, 2: {"slot": "WR", "order": 1}}, {})
    order = {r["player_id"]: r["espn_order"] for r in out}
    assert order[2] < order[1], "ESPN's entry order, not the box score's"
    assert order[3] > order[1], "a player ESPN's read lacks goes after, in box order"


def test_with_espn_slots_puts_a_seated_player_in_his_seats_place():
    rows = [{"player_id": 1, "slot": "WR"}, {"player_id": 2, "slot": "WR"}, {"player_id": 3, "slot": "RB/WR/TE"}]
    flags = {1: {"order": 0}, 2: {"order": 13}, 3: {"order": 6}}
    out = espn_season.with_espn_slots(rows, flags, {}, {1: 3, 3: 1, 9: 5})
    assert {r["player_id"]: r["espn_order"] for r in out} == {1: 6, 2: 13, 3: 0}
    gone = espn_season.with_espn_slots(rows, {1: {"order": 0}}, {}, {1: 99})
    assert gone[0]["espn_order"] == 0, "a seat whose owner left the roster falls back to his own place"


def test_parse_roster_flags_carries_injury_and_name_when_espn_says_them():
    doc = {"teams": [{"id": 7, "roster": {"entries": [
        {"playerId": 1, "lineupSlotId": 2, "playerPoolEntry": {"player": {"injuryStatus": "OUT", "fullName": "A B"}}},
        {"playerId": 2, "playerPoolEntry": {}}]}}]}
    f = espn_season.parse_roster_flags(doc, 7)
    assert f[1]["injury"] == "OUT" and f[1]["name"] == "A B" and "injury" not in f[2] and "name" not in f[2]


def test_box_scores_a_move_overtakes_are_not_kept(monkeypatch):
    """A fetch that began before a move (forget_current_boxes) read the rosters from before it: the caller gets
    them, the 60 s cache does not keep them for every other page."""
    monkeypatch.setattr(espn_season, "_BOXES", {})
    monkeypatch.setattr(espn_season, "reconcile_log", lambda lg, owners: False)

    def fetch(week):
        espn_season.forget_current_boxes(ref=home_league())  # the move lands mid-fetch
        return []
    lg = SimpleNamespace(current_week=5, box_scores=fetch)
    assert espn_season.box_scores(lg, 5, ref=home_league()) == []
    assert ("espn:100000001", 5) not in espn_season._BOXES
    lg2 = SimpleNamespace(current_week=5, box_scores=lambda week: [])
    espn_season.box_scores(lg2, 5, ref=home_league())
    assert ("espn:100000001", 5) in espn_season._BOXES, "an ordinary fetch is kept"


def test_unmatched_lists_a_sources_top_rows_espn_does_not_know():
    pool = {("jalen hurts", "QB"), ("zero guy", "TE")}
    src = {("jalen hurts", "QB"): 300.0, ("hollywood brown", "WR"): 120.0, ("deep guy", "WR"): 1.0}
    assert espn_season.unmatched(src, pool, top=2) == ["hollywood brown WR"]


def test_aliases_rename_a_source_key(monkeypatch):
    monkeypatch.setitem(espn_season.ALIASES, ("hollywood brown", "WR"), ("marquise brown", "WR"))
    assert espn_season.canon({("hollywood brown", "WR"): 120.0}) == {("marquise brown", "WR"): 120.0}


def test_aliases_are_written_normalized():
    # an entry typed as the site spells him would never match: both sides must be what norm_name makes
    for src, espn in espn_season.ALIASES.items():
        assert src == (norm_name(src[0]), src[1]) and espn == (norm_name(espn[0]), espn[1]), (src, espn)
        assert src[1] == espn[1]


def test_projection_rows_for_freezes_at_kickoff():
    now = datetime(2026, 10, 11, 17, 30, tzinfo=timezone.utc)
    ahead = {**espn_season.flatten_box_player(bp()), "kickoff": "2026-10-11T20:25:00+00:00"}
    gone = {**espn_season.flatten_box_player(bp(playerId=2, name="A.J. Brown", position="WR", proTeam="PHI")),
            "kickoff": "2026-10-11T17:00:00+00:00"}
    idle = {**espn_season.flatten_box_player(bp(playerId=3, name="Bye Guy", position="TE", proTeam="SEA",
                                                on_bye_week=True)), "kickoff": None}
    src = {"cbs": {("jahmyr gibbs", "RB"): 20.0, ("aj brown", "WR"): 14.0}}
    lookup = {1: {"ros_src": {"cbs": 200.0}}, 2: {"ros_src": {"cbs": 150.0}}, 3: {"ros_src": {"sharks": 90.0}}}
    week, ros = espn_season.projection_rows_for([ahead, gone, idle], src, lookup, now, ros_ahead=False)
    assert {(r["player_id"], r["source"]) for r in week} == {(1, "cbs"), (1, "espn")}
    assert {(r["player_id"], r["source"]) for r in ros} == {(1, "cbs"), (3, "sharks")}
    _, ros_all = espn_season.projection_rows_for([ahead, gone, idle], src, lookup, now, ros_ahead=True)
    assert {r["player_id"] for r in ros_all} == {1, 2, 3}, "ESPN's week is over: the ROS week has not begun"


def test_a_finished_week_fetches_when_the_db_cannot_answer(monkeypatch):
    _finished_week(monkeypatch, [])
    def down(kind, week=None, **k):
        raise espn_season.db.DBError("locked")
    monkeypatch.setattr(espn_season.db, "projection_rows", down)
    fetched = []
    monkeypatch.setattr(espn_season, "weekly_sources", lambda week, settings: fetched.append(week) or {})
    espn_season.week_state(week=1, injuries={}, ref=home_league())
    assert fetched == [1]


def test_concurrent_cold_sleeper_weeks_share_one_fetch(monkeypatch):
    from tests.test_flight import park, race
    monkeypatch.setattr(espn_season, "_SL", {})
    calls = []

    def fake_week(week):
        park(espn_season._SL_FLIGHT, 4, 1)
        calls.append(week)
        return []

    monkeypatch.setattr(espn_season, "fetch_sleeper_rows", fake_week)
    monkeypatch.setattr(espn_season, "parse_sleeper_rows", lambda rows, ks: {("jalen hurts", "QB"): 10.0})
    settings = {**TWO_QB, "scoring": []}
    assert race(2, lambda: espn_season._sleeper_for(4, settings)) == [{("jalen hurts", "QB"): 10.0}] * 2
    assert calls == [4]


def test_concurrent_cold_box_scores_share_one_fetch(monkeypatch):
    from tests.test_flight import park, race
    monkeypatch.setattr(espn_season, "_BOXES", {})
    calls = []
    lg = SimpleNamespace(current_week=5)

    def fetch(week):
        park(espn_season._BOXES_FLIGHT, ("espn:100000001", week, id(lg)), 1)
        calls.append(week)
        return ["box"]

    lg.box_scores = fetch
    assert race(2, lambda: espn_season.box_scores(lg, 2, ref=home_league())) == [["box"], ["box"]]
    assert calls == [2]


def test_schedule_doc_shared_across_leagues(monkeypatch):
    """The pro schedule is the NFL's, not a league's: two Leagues share one fetch, and refresh_league() drops it."""
    espn_season.refresh_league()
    fetched = []

    def get():
        fetched.append(1)
        return PRO_SCHEDULE
    lg_a = SimpleNamespace(espn_request=SimpleNamespace(get_pro_schedule=get))
    lg_b = SimpleNamespace(espn_request=SimpleNamespace(get_pro_schedule=get))
    espn_season.home_by_team(lg_a, 8)
    espn_season.home_by_team(lg_b, 8)
    espn_season.bye_by_team(lg_a)
    espn_season.bye_by_team(lg_b)
    espn_season.kickoffs_by_week(lg_b)
    assert len(fetched) == 1
    espn_season.refresh_league()
    espn_season.bye_by_team(lg_b)
    assert len(fetched) == 2


def test_schedule_parses_survive_concurrent_builds(monkeypatch):
    """Page builds run on threads: the shared parse caches must not raise while another thread inserts or evicts."""
    import sys
    import threading
    docs = [{"settings": {"proTeams": PRO_SCHEDULE["settings"]["proTeams"]}} for _ in range(2)]
    flip = [0]
    monkeypatch.setattr(espn_season, "_schedule_doc", lambda lg: docs[flip[0] % 2])
    errors = []

    def work(i):
        try:
            for n in range(300):
                flip[0] += 1
                espn_season.home_by_team(None, (i + n) % 18 + 1)
                espn_season.bye_by_team(None)
                espn_season.kickoffs_by_week(None)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    assert errors == []


def test_refresh_league_mid_call_does_not_break_the_doc():
    """refresh_league() landing mid-call leaves the call with its own document, never an IndexError."""
    espn_season.refresh_league()

    def get():
        espn_season.refresh_league()  # a refresh lands while the fetch is in flight
        return PRO_SCHEDULE
    lg = SimpleNamespace(espn_request=SimpleNamespace(get_pro_schedule=get))
    assert espn_season._schedule_doc(lg) is PRO_SCHEDULE


def test_members_of_a_league_share_one_pool_build_and_each_is_priced_against_their_own_roster(monkeypatch):
    """#475: the league half (free agents, rostered, form, status, owners) is built once for every member; the
    member's roster, `mine` and worth-to-me are dressed per member. A move (forget_current_boxes) drops it."""
    from dataclasses import replace
    calls = {"box": 0, "fa": []}
    m = _matchup()
    m.away_lineup = [as_box({**season_roster()[3], "player_id": 500, "name": "Their RB"})]
    lg = _stub_league(m, calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, ref: ref.team_id)
    real, builds = espn_season._build_league_pool, []
    monkeypatch.setattr(espn_season, "_build_league_pool",
                        lambda week, size, *a, ref: builds.append(ref.user_id) or real(1, size, {}, {}, {}, {}, ref=ref))
    a = replace(home_league(), team_id=7)
    b = replace(a, user_id=a.user_id + 1, team_id=2)
    pa, pb = espn_season.free_agent_pool(ref=a), espn_season.free_agent_pool(ref=b)
    assert builds == [a.user_id] and len(calls["fa"]) == 6, "one league build, one free-agent read per position"
    assert {r["player_id"] for r in pb["roster"]} == {500}
    assert 500 not in {r["player_id"] for r in pa["roster"]}
    them = {r["player_id"]: r for r in pa["rostered"]["RB"]}[500]
    assert them["mine"] is False and isinstance(them["add"], float)
    assert {r["player_id"]: r for r in pb["rostered"]["RB"]}[500]["mine"] is True
    assert pa["positions"]["RB"][0]["delta_roster"] != pb["positions"]["RB"][0]["delta_roster"], "against each roster"
    espn_season.forget_current_boxes(ref=a)
    espn_season.free_agent_pool(ref=b)
    assert builds == [a.user_id, b.user_id], "a move drops the league half"


def test_free_agent_reads_share_one_schedule_and_ratings_read(monkeypatch):
    """espn-api's free_agents refetched the pro schedule and the positional ratings on every call (60 of 90 calls)."""
    monkeypatch.setattr(espn_season, "_SCHED", [None])
    sched = {"settings": {"proTeams": [{"id": 1, "proGamesByScoringPeriod": {
        str(w): [{"homeProTeamId": 1, "awayProTeamId": 2, "date": 1_800_000_000_000}] for w in (1, 2)}}]}}
    counts = {"sched": 0, "ranks": 0}

    def count(name, value):
        counts[name] += 1
        return value
    lg = SimpleNamespace(year=espn_season.SEASON, espn_request=SimpleNamespace(get_pro_schedule=lambda: count("sched", sched)),
                         _get_positional_ratings=lambda w: count("ranks", {"2": {"2": 5}}))
    asked = []

    def get(view, headers=None, *, ref=None, **params):
        asked.append((view, json.loads(headers["x-fantasy-filter"])["players"], params["scoringPeriodId"]))
        return {"players": [_deep_doc(77, params["scoringPeriodId"])]}
    monkeypatch.setattr(espn_season, "raw_league_get", get)
    asks = [(pos, w) for pos in ("RB", "WR") for w in (1, 2)]
    got = espn_season._free_agent_boxes(lg, asks, 150, 2, ref=home_league())
    assert counts == {"sched": 1, "ranks": 1} and len(asked) == 4
    assert {(f["filterSlotIds"]["value"][0], f["limit"], w) for _, f, w in asked} == {(2, 150, 1), (2, 150, 2), (4, 150, 1), (4, 150, 2)}
    bp = got[("RB", 2)][0]
    assert bp.playerId == 77 and bp.projected_points == 9.5 and bp.pro_opponent == "BUF" and not bp.on_bye_week


def test_season_lookup_is_one_build_for_every_member_asking_at_once(monkeypatch):
    """A pregame pull rebuilds every member's pages at once: they share one lookup per (league, week), not one each."""
    from tests.test_flight import park, race
    espn_season._SEASON_CACHE.clear()
    builds = []

    def pool(*, ref):
        builds.append(1)
        park(espn_season._SEASON_FLIGHT, (ref.key, 7), 2)
        return SEASON_ESPN

    monkeypatch.setattr(espn_season, "fetch_pool", pool)
    monkeypatch.setattr(espn_season, "fetch_sleeper_season", lambda settings: SEASON_SLEEPER)
    monkeypatch.setattr(espn_season, "_remaining_sleeper_weeks", lambda week, settings: None)
    monkeypatch.setattr(espn_season, "ROS_SOURCES", {})
    out = race(3, lambda: espn_season.season_lookup({**TWO_QB, "scoring": []}, week=7, ref=home_league()))
    assert len(builds) == 1 and out[0] is out[1] is out[2] and isinstance(out[0], dict)
    espn_season._SEASON_CACHE.clear()


def test_fetch_injuries_is_one_download_for_every_build_asking_at_once(monkeypatch):
    from tests.test_flight import park, race
    espn_season._INJ.clear()
    gets = []

    class R:
        def raise_for_status(self): pass
        def json(self): return {"injuries": []}

    def get(*a, **k):
        gets.append(1)
        park(espn_season._INJ_FLIGHT, "injuries", 2)
        return R()

    monkeypatch.setattr(espn_season.requests, "get", get)
    assert race(3, espn_season.fetch_injuries) == [{}, {}, {}] and len(gets) == 1
    espn_season._INJ.clear()


def test_outlook_cards_unions_watched_and_reads_the_deep_ones(monkeypatch):
    """Every member's watchlist counts; a watched man deeper than the pool is read by id, a K among them dropped."""
    from dataclasses import replace
    from ffl import db, leagues, outlook
    a = home_league()
    b = replace(a, user_id=a.user_id + 1)
    row = lambda pid, pos="WR": {"player_id": pid, "pos": pos, "name": f"P{pid}", "form": None}  # noqa: E731
    pool = {"week": 6, "rostered": [row(1, "QB"), row(2, "K")], "frees": {"WR": [row(10), row(11)], "K": [row(12, "K")]},
            "enrich": lambda rows, games: [{**r, "form": games[r["player_id"]]} for r in rows]}
    monkeypatch.setattr(espn_season, "league_pool", lambda **k: pool)
    monkeypatch.setattr(leagues, "members", lambda key: [a, b] if key == a.key else [])
    monkeypatch.setattr(db, "watched_ids", lambda *, ref: {a.user_id: [11, 2], b.user_id: [20, 21, -16001]}[ref.user_id])
    news = {}
    monkeypatch.setattr(outlook, "recent_up", lambda ids, today: news.update(ids=sorted(ids)) or set())
    monkeypatch.setattr(espn_season, "fresh_league", lambda ref: "LG")
    asked = {}

    def deep(lg, ids, week, *, ref):
        asked.update(lg=lg, ids=sorted(ids), week=week)
        return [row(20, "TE"), row(21, "K")], {20: {"l3": 4}, 21: {}}
    monkeypatch.setattr(espn_season, "_watched_rows", deep)
    cards, week = espn_season.outlook_cards(ref=a)
    assert week == 6 and news["ids"] == [10, 11], "news is read for skill-position free agents only"
    assert asked == {"lg": "LG", "ids": [20, 21], "week": 6}, "only the ids the pool lacks, no D/ST id"
    assert [c["player_id"] for c in cards] == [1, 11, 20]
    assert cards[-1]["form"] == {"l3": 4}, "the deep row is enriched like the pool's"
