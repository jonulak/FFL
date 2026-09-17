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
                 "ros_proj": None, "ros_sleeper": None, "ros_cbs": None, "ros_spread": None,
                 "return_week": None, "injury_note": None, "opponent": None, "kickoff": None,
                 "home": None}


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
    assert (out[1]["season_proj"], out[1]["season_proj_sleeper"]) == (370.0, 360.0)
    assert (out[2]["season_proj"], out[2]["season_proj_sleeper"]) == (100.0, None), "a Sleeper miss keeps ESPN alone"
    assert out[3]["season_proj"] == 85.0, "ESPN WSH D/ST matches Sleeper WAS on team"
    assert out[4]["season_proj"] == 150.0, "an ESPN 0 is a placeholder, not a projection"


def test_merge_season_takes_a_dataframe_too():
    out = espn_season.merge_season(SEASON_ESPN, pd.DataFrame(SEASON_SLEEPER))
    assert out[1]["season_proj"] == 370.0 and out[3]["season_proj"] == 85.0


def test_merge_season_builds_ros_from_the_two_sources():
    ros_sl = {("jalen hurts", "QB"): 300.0, ("WAS", "D/ST"): 70.0}
    ros_cbs = {("jalen hurts", "QB"): 280.0, ("zero guy", "TE"): 90.0}
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER, ros_sl, ros_cbs)
    assert out[1]["ros_sleeper"] == 300.0 and out[1]["ros_cbs"] == 280.0
    assert out[1]["ros_proj"] == 290.0 and out[1]["ros_spread"] == 20.0
    assert out[3]["ros_proj"] == 70.0 and out[3]["ros_spread"] is None, "Sleeper alone, on team"
    assert out[4]["ros_proj"] == 90.0 and out[4]["ros_sleeper"] is None, "CBS alone"
    assert out[2]["ros_proj"] is None and out[2]["ros_spread"] is None, "neither: season.ros_proj falls back"
    assert out[1]["season_proj"] == 370.0, "the season average is untouched"


def test_merge_season_without_ros_sources_leaves_ros_empty():
    out = espn_season.merge_season(SEASON_ESPN, SEASON_SLEEPER)
    assert out[1]["ros_proj"] is None and out[1]["ros_sleeper"] is None and out[1]["ros_cbs"] is None


def test_sleeper_ros_sums_the_weeks_a_player_has_a_row_for():
    weeks = [{("a", "RB"): 10.0, ("b", "WR"): 5.0}, {("a", "RB"): 12.0}, {("a", "RB"): 8.0, ("b", "WR"): 6.5}]
    assert espn_season.sleeper_ros(weeks) == {("a", "RB"): 30.0, ("b", "WR"): 11.5}, "a bye week is simply absent"
    assert espn_season.sleeper_ros([]) == {}


def test_apply_season_overrides_only_the_ids_it_knows():
    players = [espn_season.flatten_box_player(bp(playerId=1)),
               espn_season.flatten_box_player(bp(playerId=99))]
    out = espn_season.apply_season(players, {1: {"season_proj": 370.0, "season_proj_sleeper": 360.0,
                                                 "ros_proj": 290.0, "ros_sleeper": 300.0, "ros_cbs": 280.0,
                                                 "ros_spread": 20.0}})
    assert out[0]["season_proj"] == 370.0 and out[0]["season_proj_sleeper"] == 360.0
    assert out[0]["ros_proj"] == 290.0 and out[0]["ros_spread"] == 20.0
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
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.week_state(week=1, sleeper={}, season={}, injuries={})
    assert st["week"] == 1 and st["opp"] is None
    assert "stale" not in st, "the freshness flag is stale_since, added by the API's cache"
    assert st["me"]["team"] == "Mine" and len(st["me"]["lineup"]) == 11


def test_roster_state_builds_cards_for_my_side_only(monkeypatch):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in season_roster()],
                              away_team=SimpleNamespace(team_id=2, team_name="Them"),
                              away_lineup=[as_box(season_roster()[0])])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.roster_state(week=1, sleeper={}, season={}, injuries={})
    assert st["week"] == 1 and isinstance(st["live"], bool)
    assert len(st["cards"]) == 15 and {c["group"] for c in st["cards"]} == {"starter", "bench", "ir"}
    assert all("call" in c and "reason" in c for c in st["cards"])


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


def test_parse_injuries_survives_a_return_date_that_is_not_a_date():
    """One bad row costs its own return date; the whole /week build must not fail on it."""
    row = {"shortComment": "Out indefinitely.", "details": {"returnDate": "TBD", "type": "Knee"},
           "athlete": {"links": [{"href": "https://www.espn.com/nfl/player/_/id/42/x"}]}}
    inj = espn_season.parse_injuries({"injuries": [{"displayName": "T", "injuries": [row]}]})
    assert inj[42] == {"return_date": None, "note": "Out indefinitely.", "body_part": "Knee"}


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


def test_week_state_attaches_injuries_to_both_sides(monkeypatch):
    mine, theirs = season_roster(), season_roster()
    mine[0]["player_id"] = CARTER
    theirs[0]["player_id"] = LOVE
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[as_box(p) for p in mine],
                              away_team=SimpleNamespace(team_id=8, team_name="Theirs"),
                              away_lineup=[as_box(p) for p in theirs])
    monkeypatch.setattr(espn_season, "league", lambda: SimpleNamespace(box_scores=lambda week: [matchup], current_week=1))
    monkeypatch.setattr(espn_season, "load", lambda: {**TWO_QB, "week1_start": WEEK1.isoformat()})
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.week_state(week=1, sleeper={}, season={},
                               injuries=espn_season.parse_injuries(INJ_FIXTURE))
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

    def fake_week(week, ks):
        fetched.append(week)
        if week == 17:
            raise requests.ConnectionError("sleeper down")
        return {("jalen hurts", "QB"): 10.0}

    monkeypatch.setattr(espn_season, "fetch_sleeper_week", fake_week)
    monkeypatch.setattr(espn_season, "fetch_pool", lambda: SEASON_ESPN)
    monkeypatch.setattr(espn_season, "fetch_sleeper_season", lambda: SEASON_SLEEPER)
    monkeypatch.setattr(espn_season.cbs, "fetch_ros", lambda ks: {("jalen hurts", "QB"): 150.0})
    settings = {**TWO_QB, "scoring": []}
    out = espn_season.season_lookup(settings, week=16)
    assert sorted(fetched) == [16, 17, 18]
    assert out[1]["ros_sleeper"] is None and out[1]["ros_cbs"] == 150.0 and out[1]["ros_proj"] == 150.0, \
        "one missing week would undercount every player: Sleeper's ROS is dropped, CBS carries it"
    espn_season._SEASON_CACHE.clear()
    espn_season._SL.clear()
    fetched.clear()
    out = espn_season.season_lookup(settings, week=18)
    assert fetched == [18] and out[1]["ros_sleeper"] == 10.0 and out[1]["ros_proj"] == 80.0
    assert espn_season.season_lookup(settings, week=18) is out, "cached per week"
    assert espn_season.season_lookup(settings, week=17) is not out, "another week is another sum"


def test_free_agent_pool_reports_whether_ros_came_from_the_sources(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    out = espn_season.free_agent_pool(week=1, sleeper={}, season={}, injuries={})
    assert out["ros_source"] == "season"
    out = espn_season.free_agent_pool(week=1, sleeper={}, season={1001: {"season_proj": 1.0, "season_proj_sleeper": None,
                                                                          "ros_proj": 5.0, "ros_sleeper": 5.0,
                                                                          "ros_cbs": None, "ros_spread": None}},
                                      injuries={})
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


def test_builders_share_one_box_scores_fetch(monkeypatch):
    """Every page rebuilds within the same minute or two; the 0.7 s box_scores call is made once."""
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    espn_season.week_state(week=1, sleeper={}, season={}, injuries={})
    espn_season.roster_state(week=1, sleeper={}, season={}, injuries={})
    espn_season.free_agent_pool(week=1, sleeper={}, season={}, injuries={})
    assert calls["box"] == 1
    other = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda: other)
    espn_season.roster_state(week=1, sleeper={}, season={}, injuries={})
    assert calls["box"] == 2, "a rebuilt League is a new source: its box scores are fetched again"


def test_free_agent_pool_fetches_every_position_in_position_order(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    out = espn_season.free_agent_pool(week=1, sleeper={}, season={}, injuries={})
    assert list(out["positions"]) == ["QB", "RB", "WR", "TE", "D/ST", "K"]
    assert sorted(calls["fa"]) == sorted(["QB", "RB", "WR", "TE", "D/ST", "K"]), "each fetched exactly once"
    assert all(row["slot"] == "FA" for rows in out["positions"].values() for row in rows)
