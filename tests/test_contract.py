"""The wire contract between the Python builders and the TypeScript types in web/lib/api.ts.

There are no Pydantic response models: FastAPI returns the plain dicts these functions build, so
nothing but this file stops a renamed key from reaching the UI as `undefined`. Every key set below
mirrors a type in `web/lib/api.ts` — Player, Side, LineupEntry, Swap, WaiverRow, League.standings —
and the two must change together. No network: the standings row is built from a fake league object.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from ffl import espn_season, player_card, season
from tests.test_espn_season import bp
from tests.test_season import TWO_QB, P, roster

# web/lib/api.ts: type Player
PLAYER = {"player_id", "name", "pos", "team", "slot", "bye", "injury", "proj_espn", "proj_sleeper",
          "proj", "spread", "espn_only", "actual", "game_played", "season_proj", "season_proj_sleeper",
          "ros_proj", "ros_sleeper", "ros_cbs", "ros_spread",
          "return_week", "injury_note", "opponent", "kickoff", "home", "exp", "ahead"}
# web/lib/api.ts: type LineupEntry
LINEUP_ENTRY = {"slot", "player_id", "name", "proj"}
# web/lib/api.ts: type Swap
SWAP = {"slot", "out", "in", "gain"}
# web/lib/api.ts: type Side
SIDE = {"team_id", "team", "abbrev", "logo", "lineup", "bench", "ir", "optimal", "optimal_swaps", "proj_total",
        "optimal_total", "actual_total"}
# web/lib/api.ts: type WaiverRow = Player & {...}
WAIVER_EXTRA = {"ros_proj", "ros_adj", "week_proj", "delta_roster", "delta_pool", "would_start", "week_delta", "add"}
# web/lib/api.ts: type League["standings"][number]
STANDINGS_ROW = {"rank", "team_id", "team", "abbrev", "logo", "wins", "losses", "ties", "points_for", "points_against",
                 "streak", "me"}
SCORE_SIDE = {"team_id", "team", "abbrev", "logo", "wins", "losses", "ties", "score", "proj", "me", "starter_ids"}
# web/lib/api.ts: type Week / type League top level (stale_since is added by the API's cache)
WEEK = {"week", "live", "mine", "me", "opp"}
LEAGUE = {"week", "current_week", "weeks", "playoff_teams", "live", "standings", "scoreboard"}


def test_player_keys():
    assert set(espn_season.flatten_box_player(bp())) == PLAYER


def test_side_keys():
    team = SimpleNamespace(team_id=7, team_name="Mine")
    side = espn_season.build_side(team, roster(), TWO_QB)
    assert set(side) == SIDE
    assert set(side["lineup"][0]) == PLAYER
    assert set(side["optimal"][0]) == LINEUP_ENTRY
    assert side["optimal_swaps"] and set(side["optimal_swaps"][0]) == SWAP


def test_lineup_entry_and_swap_keys():
    opt = season.optimal_lineup(roster(), TWO_QB)
    assert set(opt[0]) == LINEUP_ENTRY
    swaps = season.lineup_diff(season.current_lineup(roster()), opt)
    assert swaps and set(swaps[0]) == SWAP


def test_waiver_row_keys():
    mine = roster()
    fa = P(99, "Waiver Guy", "WR", 14, slot="FA")
    rows = season.waiver_deltas([fa], mine, mine + [fa], TWO_QB, week=3)
    assert set(rows[0]) == PLAYER | WAIVER_EXTRA


def test_standings_row_keys(monkeypatch):
    """league_state's shape, from a fake league — no ESPN call."""
    team = SimpleNamespace(team_id=7, team_name="Mine", team_abbrev="MINE", logo_url="", wins=2, losses=1, ties=0,
                           points_for=250.5, points_against=210.25, streak_type="WIN", streak_length=2)
    them = SimpleNamespace(team_id=3, team_name="Them", team_abbrev="THEM", logo_url="http://x/l.png", wins=1,
                           losses=2, ties=0, points_for=200.0, points_against=220.0, streak_type="LOSS", streak_length=1)
    bye = SimpleNamespace(home_team=team, away_team=SimpleNamespace(), home_score=0, away_score=0)
    match = SimpleNamespace(home_team=team, away_team=them, home_score=110.0, away_score=99.5,
                            home_projected=105.2, away_projected=101.0)
    future = SimpleNamespace(home_team=them, away_team=team, home_score=0, away_score=0)  # scoreboard(): no projection
    lg = SimpleNamespace(current_week=3, standings=lambda: [team, them], box_scores=lambda wk: [match, bye],
                         scoreboard=lambda wk: [future])
    monkeypatch.setattr(espn_season, "fresh_league", lambda: lg)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    monkeypatch.setattr(espn_season, "load", lambda: {"reg_season_weeks": 14, "playoff_teams": 6})
    st = espn_season.league_state(2)
    assert set(st) == LEAGUE
    assert st["week"] == 2 and st["current_week"] == 3 and st["live"] is False
    assert set(st["standings"][0]) == STANDINGS_ROW
    assert st["standings"][0]["streak"] == "W2" and st["standings"][1]["streak"] == "L1"
    assert len(st["scoreboard"]) == 1  # the bye slot is dropped
    assert set(st["scoreboard"][0]) == {"home", "away"}
    assert set(st["scoreboard"][0]["home"]) == SCORE_SIDE and st["scoreboard"][0]["home"]["me"] is True
    assert st["scoreboard"][0]["away"]["proj"] == 101.0
    ahead = espn_season.league_state(5)  # beyond current_week: schedule pairings, projection 0
    assert ahead["scoreboard"][0]["home"]["abbrev"] == "THEM" and ahead["scoreboard"][0]["home"]["proj"] == 0


def test_week_state_top_level_keys(monkeypatch):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[], away_team=SimpleNamespace(), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "fresh_league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    assert set(espn_season.week_state(week=1, sleeper={}, season={}, injuries={})) == WEEK


# web/lib/api.ts: type PlayerLog / SeasonLog / WeekLog / SeasonTotals
PLAYER_LOG = {"player_id", "name", "pos", "team", "season", "current_week", "seasons"}
SEASON_LOG = {"season", "weeks", "totals"}
WEEK_LOG = {"week", "opp", "home", "status", "pts", "summary", "box"}
SEASON_TOTALS = {"games", "pts", "ppg", "summary", "box"}
FX = Path(__file__).parent / "fixtures"


def _allen() -> dict:
    card = json.loads((FX / "espn_playercard.json").read_text())
    return {"players": [p for p in card["players"] if p["player"]["id"] == 3918298]}


def test_player_log_keys():
    scoring = json.loads((FX / "settings_scoring.json").read_text())["scoring"]
    log = player_card.player_log(_allen(), scoring, {2025: json.loads((FX / "pro_schedule_2025.json").read_text())}, 4, 2026)
    assert set(log) == PLAYER_LOG
    s = log["seasons"][-1]
    assert set(s) == SEASON_LOG and set(s["weeks"][0]) == WEEK_LOG and set(s["totals"]) == SEASON_TOTALS


def test_player_card_io(monkeypatch):
    """espn_season.player_card wires the fetch to player_log: one kona_playercard call, both
    schedules, the league's scoring; a failed schedule read leaves opponents unknown."""
    seen = {}

    def fake_get(view, extend="", headers=None, **params):
        seen["view"], seen["filter"] = view, json.loads(headers["x-fantasy-filter"])
        return _allen()

    def down(*a):
        raise RuntimeError("down")
    monkeypatch.setattr(espn_season, "raw_league_get", fake_get)
    monkeypatch.setattr(espn_season, "fresh_league", lambda: SimpleNamespace(current_week=4))
    monkeypatch.setattr(espn_season, "load", lambda: json.loads((FX / "settings_scoring.json").read_text()))
    monkeypatch.setattr(espn_season, "_schedule_doc", down)
    monkeypatch.setattr(espn_season, "past_schedule", down)
    rows24 = json.loads((FX / "espn_playercard_2024.json").read_text())["players"][0]["player"]["stats"]
    past = []

    def past_stats(season, pid):
        past.append(season)
        if season == 2023:
            raise RuntimeError("down")  # one season failing leaves the others
        return rows24
    monkeypatch.setattr(espn_season, "past_stats", past_stats)
    log = espn_season.player_card(3918298)
    assert seen["view"] == "kona_playercard" and seen["filter"]["players"]["filterIds"]["value"] == [3918298]
    assert set(log) == PLAYER_LOG and log["seasons"][1]["weeks"][0]["opp"] is None
    assert sorted(past) == [2023, 2024]
    assert [s["season"] for s in log["seasons"]] == [2026, 2025, 2024]
