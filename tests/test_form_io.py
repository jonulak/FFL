"""Recent form, the I/O side: ESPN's weekly stat rows and Sleeper's snaps flattened to the game
rows season.recent_form reads, and the builders carrying `form` on every player (no network)."""
from types import SimpleNamespace

from ffl import espn_season, season
from tests.test_espn_season import TWO_QB, _matchup, _stub_league, as_box, bp
from tests.test_season import roster as season_roster


def week_stats(points, proj, **breakdown):
    return {"points": points, "projected_points": proj, "breakdown": breakdown}


def test_game_from_stats_reads_opportunity_by_position():
    rb = espn_season.game_from_stats(2, week_stats(4.5, 9.58, rushingAttempts=2, receivingTargets=4,
                                                   receivingReceptions=3, receivingYards=5), "RB", final=True)
    assert rb == {"week": 2, "pts": 4.5, "proj": 9.58, "opp": 6.0, "tgt": 4.0, "rec_yd": 5.0,
                  "snaps": None, "team_snaps": None, "final": True, "active": True}
    qb = espn_season.game_from_stats(1, week_stats(20, 18, passingAttempts=30, rushingAttempts=4), "QB", final=True)
    assert qb["opp"] == 34.0 and qb["active"]
    k = espn_season.game_from_stats(1, week_stats(9, 8, attemptedFieldGoals=2, attemptedExtraPoints=3), "K", final=True)
    assert k["opp"] == 5.0 and k["active"]
    dst = espn_season.game_from_stats(1, week_stats(8, 7, defensiveSacks=2, teamWin=1), "D/ST", final=True)
    assert dst["opp"] is None and dst["active"], "a defence's game is any stat line"
    idle = espn_season.game_from_stats(3, week_stats(0, 9, teamLoss=1), "RB", final=True)
    assert idle["opp"] == 0.0 and not idle["active"], "dressed but no touch: not a game he played"
    none = espn_season.game_from_stats(3, {"projected_points": 9}, "WR", final=False)
    assert none["pts"] == 0.0 and not none["active"] and not none["final"], "a row before kickoff"


def test_player_games_takes_the_weeks_asked_for_and_marks_the_current_one_by_game_played():
    p = SimpleNamespace(position="RB", game_played=0, stats={
        0: {"points": 7.3, "breakdown": {"rushingAttempts": 7}},  # the season total row
        1: week_stats(2.8, 12.0, rushingAttempts=5, receivingTargets=1),
        2: week_stats(4.5, 9.6, rushingAttempts=2, receivingTargets=4),
        3: week_stats(0.0, 9.0)})
    games = espn_season.player_games(p, [1, 2, 3], week=3)
    assert [g["week"] for g in games] == [1, 2, 3]
    assert games[0]["final"] and games[1]["final"], "past weeks are final"
    assert not games[2]["final"], "this week's game has not been played (game_played 0)"
    p.game_played = 100
    assert espn_season.player_games(p, [3], week=3)[0]["final"]
    assert espn_season.player_games(SimpleNamespace(position="RB"), [1, 2], week=2) == [], "no stats at all"


def test_parse_sleeper_snaps_keys_like_the_projections_and_skips_rows_without_snaps():
    rows = [{"player": {"first_name": "Kenny", "last_name": "Gainwell", "position": "RB", "team": "TB"},
             "stats": {"off_snp": 15.0, "tm_off_snp": 60.0, "rec_tgt": 4.0}},
            {"player": {"first_name": "Jayden", "last_name": "Reed", "position": "WR", "team": "GB"},
             "stats": {"rec_tgt": 7.0}},  # week 2 on a Monday: snaps not in yet
            {"player": {"first_name": "", "last_name": "", "position": "DEF", "team": "CIN"},
             "stats": {"gp": 1.0}},
            {"player": {"first_name": "Bryce", "last_name": "Young", "position": "QB", "team": "CAR"},
             "stats": {"off_snp": 62.0, "tm_off_snp": 62.0}}]
    out = espn_season.parse_sleeper_snaps(rows)
    assert out == {("kenny gainwell", "RB"): (15.0, 60.0), ("bryce young", "QB"): (62.0, 62.0)}


def test_parse_bye_by_team():
    data = {"settings": {"proTeams": [{"id": 0, "abbrev": "FA", "byeWeek": 0},
                                      {"id": 11, "abbrev": "IND", "byeWeek": 13},
                                      {"id": 12, "abbrev": "KC", "byeWeek": 5}]}}
    assert espn_season.parse_bye_by_team(data) == {"IND": 13, "KC": 5}, "team 0 is the placeholder"


def _players():
    return [espn_season.flatten_box_player(bp(playerId=1, name="Kenny Gainwell", proTeam="TB")),
            espn_season.flatten_box_player(bp(playerId=2, name="Jayden Reed", position="WR", proTeam="GB"))]


def test_apply_form_joins_games_and_snaps_and_prices_pace_against_ros():
    games = {1: [espn_season.game_from_stats(1, week_stats(2.8, 12.0, rushingAttempts=5, receivingTargets=1, receivingYards=3), "RB", True),
                 espn_season.game_from_stats(2, week_stats(4.5, 9.6, rushingAttempts=2, receivingTargets=4, receivingYards=5), "RB", True)]}
    snaps = {1: {("kenny gainwell", "RB"): (15.0, 60.0)}, 2: {}}
    players = _players()
    players[0]["ros_proj"] = 132.4
    out = espn_season.apply_form(players, games, snaps, week=3, byes={"TB": 9})
    f = out[0]["form"]
    assert f["games"] == 2 and f["weeks"] == [1, 2]
    assert f["snap_pct"] == 25 and f["snap_games"] == 1, "week 2's snaps are not in yet: one game's worth"
    assert f["tgt_snap"] == round(1 / 15, 2), "targets over the snapped games only"
    assert f["ypt"] == round(8 / 5, 1)
    assert f["pace_gap"] == round(3.65 * season.games_left(3, 9) - 132.4, 1)
    assert out[1]["form"] is None, "no games: nothing, not zeros"
    assert "form" not in players[0], "pure: the input dicts are untouched"


def test_apply_form_uses_the_season_formula_ros_when_no_source_has_him():
    games = {1: [espn_season.game_from_stats(1, week_stats(10, 8, rushingAttempts=10), "RB", True)]}
    players = _players()  # season_proj 320.5, ros_proj None
    f = espn_season.apply_form(players, games, {}, week=2, byes={})[0]["form"]
    assert f["pace_gap"] == round(10 * season.games_left(2, None) - season.ros_proj(players[0], 2), 1)


def test_builders_carry_form_from_the_box_score_alone_when_the_league_has_no_roster_history(monkeypatch):
    """A stub League without `teams` or a schedule: form comes from this week's box rows only."""
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.roster_state(week=1, sleeper={}, season={}, injuries={}, snaps={})
    assert all(c["form"] is None for c in st["cards"]), "the stub rows have no stats: no games"
    pool = espn_season.free_agent_pool(week=1, sleeper={}, season={}, injuries={}, snaps={})
    assert all(r["form"] is None for rows in pool["positions"].values() for r in rows)
    assert calls["box"] == 1 and sorted(calls["fa"]) == sorted(["QB", "RB", "WR", "TE", "D/ST", "K"]), \
        "week 1: no earlier weeks to fetch"


def test_free_agent_pool_fetches_the_form_weeks_and_reads_their_stats(monkeypatch):
    calls = {"box": 0, "fa": []}
    weeks_asked = []

    def free_agents(week, size, position):
        weeks_asked.append((position, week))
        fa = as_box({**season_roster()[0], "player_id": 1000, "slot": "FA"})
        fa.stats = {week: week_stats(10.0 + week, 9.0, rushingAttempts=8, receivingTargets=2, receivingYards=10)}
        return [fa]
    lg = _stub_league(_matchup(), calls)
    lg.free_agents = free_agents
    lg.current_week = 3
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    pool = espn_season.free_agent_pool(week=3, sleeper={}, season={}, injuries={}, snaps={})
    assert sorted(w for p, w in weeks_asked if p == "RB") == [1, 2, 3], "the window's weeks, once each"
    row = pool["positions"]["RB"][0]
    assert row["form"]["weeks"] == [1, 2], "week 3's game (game_played 0) is not final"
    assert row["form"]["l3"] == round((11 + 12) / 2, 1)


def test_roster_state_reads_earlier_weeks_off_the_team_roster(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    lg.current_week = 3
    mine = season_roster()[3]  # RB A, id 4
    hist = SimpleNamespace(playerId=mine["player_id"], position="RB", stats={
        1: week_stats(15.0, 14.0, rushingAttempts=18, receivingTargets=3, receivingYards=20),
        2: week_stats(22.0, 14.5, rushingAttempts=20, receivingTargets=5, receivingYards=40)})
    lg.teams = [SimpleNamespace(team_id=7, roster=[hist]), SimpleNamespace(team_id=2, roster=[])]
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.roster_state(week=3, sleeper={}, season={}, injuries={}, snaps={})
    card = next(c for c in st["cards"] if c["player_id"] == 4)
    assert card["form"]["weeks"] == [1, 2] and card["form"]["beat"] == round(((15 - 14) + (22 - 14.5)) / 2, 1)
    assert card["form"]["opp_trend"] == 25.0 - 21.0


def test_free_agent_pool_carries_my_roster_with_the_same_figures(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    pool = espn_season.free_agent_pool(week=1, sleeper={}, season={}, injuries={}, snaps={})
    mine = pool["roster"]
    assert len(mine) == 15 and all("form" in p and "ros_proj" in p and "week_proj" in p for p in mine)
    assert [p["ros_proj"] for p in mine] == sorted((p["ros_proj"] for p in mine), reverse=True), "by ROS, best first"
    assert any(p["slot"] == "IR" for p in mine), "IR players stay in: a drop is a drop"
