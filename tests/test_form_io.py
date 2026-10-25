"""Recent form, the I/O side: ESPN's weekly stat rows and Sleeper's snaps flattened to the game
rows season.recent_form reads, and the builders carrying `form` on every player (no network)."""
import json
from types import SimpleNamespace

from ffl import db, espn_season, season
from tests.test_espn_season import TWO_QB, _matchup, _stub_league, as_box, bp
from tests.test_season import roster as season_roster


def week_stats(points, proj, **breakdown):
    return {"points": points, "projected_points": proj, "breakdown": breakdown}


def test_game_row_carries_carries_receptions_and_pass_attempts():
    g = espn_season.game_from_stats(3, week_stats(12, 10, rushingAttempts=14, receivingReceptions=3,
                                                  receivingTargets=4, passingAttempts=0), "RB", final=True)
    assert (g["car"], g["rec"], g["tgt"], g["pass_att"]) == (14, 3, 4, 0)


def test_game_from_stats_reads_opportunity_by_position():
    rb = espn_season.game_from_stats(2, week_stats(4.5, 9.58, rushingAttempts=2, receivingTargets=4,
                                                   receivingReceptions=3, receivingYards=5), "RB", final=True)
    assert rb == {"week": 2, "pts": 4.5, "proj": 9.58, "opp": 6.0, "tgt": 4.0, "rec_yd": 5.0,
                  "car": 2.0, "rec": 3.0, "pass_att": 0.0,
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


def test_parse_sleeper_usage_reads_air_yards_and_red_zone_against_the_clubs_passing():
    rows = [{"team": "GB", "player": {"first_name": "Christian", "last_name": "Watson", "position": "WR"},
             "stats": {"off_snp": 50.0, "tm_off_snp": 60.0, "rec_air_yd": 131.0, "rec": 6.0, "rec_tgt": 8.0}},
            {"team": "GB", "player": {"first_name": "Jordan", "last_name": "Love", "position": "QB"},
             "stats": {"off_snp": 60.0, "tm_off_snp": 60.0, "pass_air_yd": 252.0, "pass_cmp": 21.0, "pass_rz_att": 5.0}},
            {"team": "GB", "player": {"first_name": "Josh", "last_name": "Jacobs", "position": "RB"},
             "stats": {"off_snp": 40.0, "tm_off_snp": 60.0, "rush_rz_att": 3.0}},
            {"team": "CAR", "player": {"first_name": "Tetairoa", "last_name": "McMillan", "position": "WR"},
             "stats": {"rec_air_yd": 90.0}}]  # snaps not in yet: the game has not landed
    out = espn_season.parse_sleeper_usage(rows)
    assert out[("christian watson", "WR")] == {"air_yd": 131.0, "catches": 6.0, "team_air": 252.0, "rz_tgt": 0.0,
                                               "rz_att": 0.0, "rz_pass": 0.0, "pass_air": 0.0, "cmp": 0.0}
    assert out[("jordan love", "QB")]["pass_air"] == 252.0 and out[("jordan love", "QB")]["rz_pass"] == 5.0 and out[("jordan love", "QB")]["cmp"] == 21.0
    assert out[("josh jacobs", "RB")]["rz_att"] == 3.0
    assert ("tetairoa mcmillan", "WR") not in out


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


def test_apply_form_joins_air_yards_and_red_zone_by_name_and_position():
    games = {1: [espn_season.game_from_stats(1, week_stats(9.0, 8.0, rushingAttempts=10, receivingTargets=4), "RB", True)]}
    usage = {1: {("kenny gainwell", "RB"): {"air_yd": 12.0, "catches": 3.0, "team_air": 240.0, "rz_tgt": 1.0, "rz_att": 2.0,
                                            "rz_pass": 0.0, "pass_air": 0.0}}}
    f = espn_season.apply_form(_players(), games, {}, week=2, byes={}, usage_by_week=usage)[0]["form"]
    assert f["air_games"] == 1 and f["air_share"] == 5 and f["air_catch"] == 4.0 and f["rz_att"] == 2.0
    assert espn_season.apply_form(_players(), games, {}, week=2, byes={})[0]["form"]["air_games"] == 0


def test_apply_form_uses_the_season_formula_ros_when_no_source_has_him():
    games = {1: [espn_season.game_from_stats(1, week_stats(10, 8, rushingAttempts=10), "RB", True)]}
    players = _players()  # season_proj 320.5, ros_proj None
    f = espn_season.apply_form(players, games, {}, week=2, byes={})[0]["form"]
    assert f["pace_gap"] == round(10 * season.games_left(2, None) - season.ros_proj(players[0], 2), 1)


def test_builders_carry_form_from_the_box_score_alone_when_the_league_has_no_roster_history(monkeypatch):
    """A stub League without `teams` or a schedule: form comes from this week's box rows only."""
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={})
    assert all(c["form"] is None for c in st["cards"]), "the stub rows have no stats: no games"
    pool = espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={})
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
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    pool = espn_season.free_agent_pool(week=3, sources={}, season={}, injuries={}, snaps={})
    assert sorted(w for p, w in weeks_asked if p == "RB") == [1, 2, 3], "the window's weeks, once each"
    row = pool["positions"]["RB"][0]
    assert row["form"]["weeks"] == [1, 2], "week 3's game (game_played 0) is not final"
    assert row["form"]["l3"] == round((11 + 12) / 2, 1)


def _week_only(bp, w):
    return SimpleNamespace(playerId=bp.playerId, position=bp.position, game_played=100, stats={w: bp.stats[w]})


def test_roster_state_reads_earlier_weeks_off_those_weeks_box_scores(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    lg.current_week = 3
    mine = season_roster()[3]  # RB A, id 4
    hist = SimpleNamespace(playerId=mine["player_id"], position="RB", stats={
        1: week_stats(15.0, 14.0, rushingAttempts=18, receivingTargets=3, receivingYards=20),
        2: week_stats(22.0, 14.5, rushingAttempts=20, receivingTargets=5, receivingYards=40)})
    hist.game_played = 100
    now_boxes = espn_season._boxes_by_team
    monkeypatch.setattr(espn_season, "_boxes_by_team",
                        lambda lg, w: now_boxes(lg, w) if w == 3 else {7: [_week_only(hist, w)]})
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.roster_state(week=3, sources={}, season={}, injuries={}, snaps={})
    card = next(c for c in st["cards"] if c["player_id"] == 4)
    assert card["form"]["weeks"] == [1, 2] and card["form"]["beat"] == round(((15 - 14) + (22 - 14.5)) / 2, 2)
    assert card["form"]["opp_trend"] == 25.0 - 21.0


def test_free_agent_pool_carries_my_roster_with_the_same_figures(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    pool = espn_season.free_agent_pool(week=1, sources={}, season={}, injuries={}, snaps={})
    mine = pool["roster"]
    assert len(mine) == 15 and all("form" in p and "ros_proj" in p and "week_proj" in p for p in mine)
    assert [p["ros_adj"] for p in mine] == sorted((p["ros_adj"] for p in mine), reverse=True), "by ROS, best first"
    assert any(p["slot"] == "IR" for p in mine), "IR players stay in: a drop is a drop"


# --- the league's strategy, "Usage-adjusted ROS" (`uv run python -m ffl.strategy show`): the I/O side -------------------------------------------

def test_apply_form_weighs_each_game_against_that_weeks_position_reference():
    games = {1: [espn_season.game_from_stats(1, week_stats(10, 8, rushingAttempts=10), "RB", True),
                 espn_season.game_from_stats(2, week_stats(12, 8, rushingAttempts=12), "RB", True)]}
    snaps = {1: {("kenny gainwell", "RB"): (30.0, 60.0)}, 2: {("kenny gainwell", "RB"): (60.0, 60.0)}}
    refs = {1: {"RB": 0.6}, 2: {"RB": 0.6}}
    f = espn_season.apply_form(_players(), games, snaps, week=3, byes={}, refs=refs)[0]["form"]
    assert f["weight"] == round(min(1, 0.5 / 0.6) + 1.0, 2)
    bare = espn_season.apply_form(_players(), games, snaps, week=3, byes={})[0]["form"]
    assert bare["weight"] == 2.0, "no references: every game a full one"


def test_parse_sleeper_exp_reads_years_in_the_league_keyed_like_the_snaps():
    rows = [{"player": {"first_name": "Jonah", "last_name": "Coleman", "position": "RB", "years_exp": 0}, "stats": {}},
            {"player": {"first_name": "Kenny", "last_name": "Gainwell", "position": "RB", "years_exp": 6}, "stats": {}},
            {"player": {"first_name": "Nobody", "last_name": "Known", "position": "WR"}, "stats": {}}]
    assert espn_season.parse_sleeper_exp(rows) == {("jonah coleman", "RB"): 0, ("kenny gainwell", "RB"): 6}


def test_apply_exp_sets_years_where_sleeper_has_him_and_leaves_none_otherwise():
    out = espn_season.apply_exp(_players(), {("kenny gainwell", "RB"): 6})
    assert out[0]["exp"] == 6 and out[1]["exp"] is None


def test_roster_games_read_earlier_weeks_off_those_weeks_box_scores(monkeypatch):
    """lg.teams rosters carry only the current period (ffl-rns, ffl-sba), so earlier weeks come from
    each week's box scores, for every club's players. A week he was on no roster comes off that week's
    player info (ffl-iig), which the conftest stub answers with nothing here."""
    def box(pid, w, pts):
        return SimpleNamespace(playerId=pid, position="RB", game_played=100,
                               stats={w: week_stats(pts, 14.0, rushingAttempts=12)})
    past = {1: {7: [box(4, 1, 15.0)], 2: [box(1, 1, 20.0)]},
            2: {7: [box(4, 2, 11.0)], 2: [box(1, 2, 8.0)]}}
    monkeypatch.setattr(espn_season, "_boxes_by_team", lambda lg, w: past[w])
    now = {7: [box(4, 3, 9.0), box(5, 3, 6.0)], 2: [box(1, 3, 7.0)]}  # 5 joined a roster this week
    games = espn_season._roster_games(SimpleNamespace(teams=[]), now, week=3)
    assert [g["week"] for g in games[4]] == [1, 2, 3]
    assert [g["week"] for g in games[1]] == [1, 2, 3] and games[1][0]["pts"] == 20.0, "another club's history too"
    assert [g["week"] for g in games[5]] == [3], "ESPN has no free-agent weeks for him (the stub answers {})"


def _info_doc(pid: int, w: int, pts: float, proj: float, carries: int) -> dict:
    """One player as kona_player_info answers for scoringPeriodId w: that week's actual and projected rows."""
    def row(source, total):
        return {"seasonId": espn_season.SEASON, "scoringPeriodId": w, "statSourceId": source, "statSplitTypeId": 1,
                "appliedTotal": total, "stats": {"23": carries}}
    return {"id": pid, "player": {"id": pid, "fullName": f"Player {pid}", "eligibleSlots": [2, 20], "proTeamId": 1,
                                  "defaultPositionId": 2, "stats": [row(0, pts), row(1, proj)]}}


def _info_by_week(docs: dict[tuple[int, int], dict], asked: list):
    """A raw_league_get stub: kona_player_info by ids and week, recording each ask as (week, ids)."""
    def get(view, headers=None, **params):
        assert view == "kona_player_info"
        ids = json.loads(headers["x-fantasy-filter"])["players"]["filterIds"]["value"]
        w = params["scoringPeriodId"]
        asked.append((w, sorted(ids)))
        return {"players": [docs[(pid, w)] for pid in ids if (pid, w) in docs]}
    return get


def _rb_box(pid, w, pts, proj=14.0):
    return SimpleNamespace(playerId=pid, position="RB", game_played=100,
                           stats={w: week_stats(pts, proj, rushingAttempts=12)})


def test_roster_games_fill_a_pickups_free_agent_weeks_off_that_weeks_player_info(monkeypatch):
    """ffl-iig: a week he sat on no fantasy roster comes off that week's player info, one call a week for
    every player missing from it, and is read once for the process."""
    past = {1: {7: [_rb_box(4, 1, 15.0)]}, 2: {7: [_rb_box(4, 2, 11.0)]}}
    monkeypatch.setattr(espn_season, "_boxes_by_team", lambda lg, w: past[w])
    asked = []
    docs = {(5, 1): _info_doc(5, 1, 6.0, 4.0, 5), (5, 2): _info_doc(5, 2, 18.0, 5.0, 16),
            (6, 2): _info_doc(6, 2, 3.0, 2.0, 2)}
    monkeypatch.setattr(espn_season, "raw_league_get", _info_by_week(docs, asked))
    past[1][2] = [_rb_box(6, 1, 9.0)]  # 6 was rostered in week 1, dropped for week 2, re-added now
    now = {7: [_rb_box(4, 3, 9.0), _rb_box(5, 3, 21.0), _rb_box(6, 3, 7.0)]}
    games = espn_season._roster_games(SimpleNamespace(teams=[]), now, week=3)
    assert sorted(asked) == [(1, [5]), (2, [5, 6])], "one call a week, only for the players no box had"
    assert [g["week"] for g in games[5]] == [1, 2, 3]
    assert [(g["pts"], g["proj"], g["car"]) for g in games[5][:2]] == [(6.0, 4.0, 5.0), (18.0, 5.0, 16.0)], \
        "the week's points and its projection, as a free agent's row reads"
    assert [g["week"] for g in games[6]] == [1, 2, 3] and games[6][0]["pts"] == 9.0, "box week kept, gap filled, once each"
    assert [g["week"] for g in games[4]] == [1, 2, 3], "a man rostered all along asks for nothing"
    espn_season._roster_games(SimpleNamespace(teams=[]), now, week=3)
    assert len(asked) == 2, "a finished week is read once for the process"


def test_roster_games_ask_nothing_for_a_rostered_man_on_bye(monkeypatch):
    """In a lineup that week but without a game (a bye, a week out): rostered, so no gap and no call."""
    bye = SimpleNamespace(playerId=4, position="RB", game_played=100, stats={})
    monkeypatch.setattr(espn_season, "_boxes_by_team", lambda lg, w: {7: [bye]})
    monkeypatch.setattr(espn_season, "raw_league_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    games = espn_season._roster_games(SimpleNamespace(teams=[]), {7: [_rb_box(4, 3, 9.0)]}, week=3)
    assert [g["week"] for g in games[4]] == [3]


def test_roster_games_keep_the_box_weeks_when_the_player_info_read_fails(monkeypatch):
    past = {1: {7: [_rb_box(4, 1, 15.0)]}, 2: {7: [_rb_box(4, 2, 11.0)]}}
    monkeypatch.setattr(espn_season, "_boxes_by_team", lambda lg, w: past[w])
    asked = []

    def get(view, headers=None, **params):
        asked.append(params["scoringPeriodId"])
        if params["scoringPeriodId"] == 1:
            raise RuntimeError("ESPN 500")
        return {"players": [_info_doc(5, 2, 18.0, 5.0, 16)]}
    monkeypatch.setattr(espn_season, "raw_league_get", get)
    now = {7: [_rb_box(4, 3, 9.0), _rb_box(5, 3, 21.0)]}
    games = espn_season._roster_games(SimpleNamespace(teams=[]), now, week=3)
    assert [g["week"] for g in games[5]] == [2, 3] and [g["week"] for g in games[4]] == [1, 2, 3]
    espn_season._roster_games(SimpleNamespace(teams=[]), now, week=3)
    assert sorted(asked) == [1, 1, 2], "the failed week is asked again; the read one is not"


def test_roster_games_ask_no_player_info_for_a_week_whose_box_scores_failed(monkeypatch):
    """A failed week's box scores leave it empty for everyone: it is not filled from player info."""
    def boxes(lg, w):
        if w == 1:
            raise RuntimeError("ESPN 500")
        return {7: [_rb_box(4, 2, 11.0)]}
    monkeypatch.setattr(espn_season, "_boxes_by_team", boxes)
    asked = []
    monkeypatch.setattr(espn_season, "raw_league_get", _info_by_week({(5, 2): _info_doc(5, 2, 18.0, 5.0, 16)}, asked))
    games = espn_season._roster_games(SimpleNamespace(teams=[]), {7: [_rb_box(4, 3, 9.0), _rb_box(5, 3, 21.0)]}, week=3)
    assert asked == [(2, [5])] and [g["week"] for g in games[5]] == [2, 3]


def test_gap_games_split_a_weeks_ask_by_batch_and_skip_an_unreadable_player(monkeypatch):
    asked = []
    docs = {(pid, 2): _info_doc(pid, 2, 5.0, 4.0, 3) for pid in range(100, 100 + espn_season.GAP_BATCH + 1)}
    docs[(100, 2)] = {"id": 100, "player": {"id": 100}}  # no proTeamId: espn-api's Player raises on it
    monkeypatch.setattr(espn_season, "raw_league_get", _info_by_week(docs, asked))
    out = espn_season._gap_games({pid: [2] for pid, _ in docs}, week=3)
    assert sorted(len(ids) for _, ids in asked) == [1, espn_season.GAP_BATCH]
    assert 100 not in out and len(out) == espn_season.GAP_BATCH, "only the unreadable player loses his row"


def test_gap_games_come_from_the_table_without_asking_espn(monkeypatch):
    rows = [{"week": 2, "pts": 7.0}]
    db.put_gap_rows(2, {5: rows}, 1.0)
    monkeypatch.setattr(espn_season, "raw_league_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    assert espn_season._gap_games({5: [2]}, week=3) == {5: rows}


def test_gap_games_read_from_espn_are_written_to_the_table(monkeypatch):
    asked = []
    monkeypatch.setattr(espn_season, "raw_league_get", _info_by_week({(5, 2): _info_doc(5, 2, 18.0, 5.0, 16)}, asked))
    out = espn_season._gap_games({5: [2]}, week=3)
    assert db.gap_rows(2, [5]) == {5: out[5]}
    espn_season._GAP_GAMES.clear()
    assert espn_season._gap_games({5: [2]}, week=3) == out
    assert len(asked) == 1, "the second ask came off the table"


def test_gap_games_fall_through_to_espn_when_the_table_errors(monkeypatch):
    def boom(*a, **k):
        raise db.DBError("down")
    monkeypatch.setattr(db, "gap_rows", boom)
    monkeypatch.setattr(db, "put_gap_rows", boom)
    asked = []
    monkeypatch.setattr(espn_season, "raw_league_get", _info_by_week({(5, 2): _info_doc(5, 2, 18.0, 5.0, 16)}, asked))
    assert [g["week"] for g in espn_season._gap_games({5: [2]}, week=3)[5]] == [2]
    assert len(asked) == 1


def test_roster_games_ask_nothing_in_week_one(monkeypatch):
    monkeypatch.setattr(espn_season, "raw_league_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked")))
    games = espn_season._roster_games(SimpleNamespace(teams=[]), {7: [_rb_box(5, 1, 21.0)]}, week=1)
    assert [g["week"] for g in games[5]] == [1]


def test_free_agent_pool_starts_ros_from_the_next_week_once_this_weeks_games_are_played(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    lg.current_week = 3
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "ros_week_for", lambda lg, week: 4)
    asked = []
    monkeypatch.setattr(espn_season, "_season_for", lambda settings, season, week: asked.append(week) or {})
    pool = espn_season.free_agent_pool(week=3, sources={}, injuries={}, snaps={})
    assert pool["week"] == 3 and pool["ros_week"] == 4 and pool["week_over"] is True
    assert asked == [4], "the Sleeper sum starts from the ROS week"
    rows = [r for rows in pool["positions"].values() for r in rows]
    assert all(r["week_delta"] == 0.0 and not r["would_start"] for r in rows), "nothing left to gain this week"
    monkeypatch.setattr(espn_season, "ros_week_for", lambda lg, week: 3)
    live = espn_season.free_agent_pool(week=3, sources={}, season={}, injuries={}, snaps={})
    assert live["ros_week"] == 3 and live["week_over"] is False


def test_free_agent_pool_snapshots_the_blend_once_per_ros_week(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    lg.current_week = 3
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    espn_season.free_agent_pool(week=3, sources={}, season={}, injuries={}, snaps={})
    first = db.snapshots()
    assert [s["week"] for s in first] == [3]
    espn_season.free_agent_pool(week=3, sources={}, season={}, injuries={}, snaps={})
    assert db.snapshots() == first, "the first build of the week stands"


def test_a_finished_weeks_box_scores_are_read_once_for_the_process(monkeypatch):
    """ffl-qf7: weeks before the current one never change; only the current week runs on BOXES_TTL_S."""
    asked = []

    def league():
        return SimpleNamespace(current_week=3, box_scores=lambda w: asked.append(w) or [w])
    clock = [1000.0]
    monkeypatch.setattr(espn_season.time, "time", lambda: clock[0])
    lg = league()
    assert espn_season.box_scores(lg, 1) == [1] and espn_season.box_scores(lg, 3) == [3]
    clock[0] += espn_season.BOXES_TTL_S * 10
    rebuilt = league()  # fresh_league after an hour: a new League object
    assert espn_season.box_scores(rebuilt, 1) == [1] and espn_season.box_scores(rebuilt, 3) == [3]
    assert asked == [1, 3, 3], "week 1 once; the current week again past the TTL"


def test_roster_state_drops_a_week_whose_box_scores_fail_instead_of_failing(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    lg.current_week = 3
    mine = season_roster()[3]  # RB A, id 4
    hist = SimpleNamespace(playerId=mine["player_id"], position="RB", stats={
        2: week_stats(22.0, 14.5, rushingAttempts=20, receivingTargets=5, receivingYards=40)})
    hist.game_played = 100
    now_boxes = espn_season._boxes_by_team

    def boxes(lg, w):
        if w == 1:
            raise RuntimeError("ESPN 500")
        return now_boxes(lg, w) if w == 3 else {7: [_week_only(hist, w)]}
    monkeypatch.setattr(espn_season, "_boxes_by_team", boxes)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    st = espn_season.roster_state(week=3, sources={}, season={}, injuries={}, snaps={})
    card = next(c for c in st["cards"] if c["player_id"] == 4)
    assert card["form"]["weeks"] == [2], "week 1 has no rows; the page still builds"


def test_roster_state_uses_the_flags_it_is_handed(monkeypatch):
    calls = {"box": 0, "fa": []}
    lg = _stub_league(_matchup(), calls)
    monkeypatch.setattr(espn_season, "league", lambda *a, **k: lg)
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg: (_ for _ in ()).throw(AssertionError("read again")))
    st = espn_season.roster_state(week=1, sources={}, season={}, injuries={}, snaps={}, flags={4: {"slot": "BE", "locked": True}})
    card = next(c for c in st["cards"] if c["player_id"] == 4)
    assert card["slot"] == "BE" and card["locked"] is True
