"""The wire contract between the Python builders and the TypeScript types in web/lib/api.ts.

There are no Pydantic response models: FastAPI returns the plain dicts these functions build, so
nothing but this file stops a renamed key from reaching the UI as `undefined`. Every key set below
mirrors a type in `web/lib/api.ts` — Player, Side, LineupEntry, Swap, WaiverRow, League.standings —
and the two must change together. No network: the standings row is built from a fake league object.
"""
from types import SimpleNamespace

from ffl import espn_season, season
from tests.test_espn_season import bp
from tests.test_season import TWO_QB, P, roster

# web/lib/api.ts: type Player
PLAYER = {"player_id", "name", "pos", "team", "slot", "bye", "injury", "proj_espn", "proj_sleeper",
          "proj", "spread", "espn_only", "actual", "game_played", "season_proj", "season_proj_sleeper",
          "return_week", "injury_note", "opponent", "kickoff"}
# web/lib/api.ts: type LineupEntry
LINEUP_ENTRY = {"slot", "player_id", "name", "proj"}
# web/lib/api.ts: type Swap
SWAP = {"slot", "out", "in", "gain"}
# web/lib/api.ts: type Side
SIDE = {"team_id", "team", "lineup", "bench", "ir", "optimal", "optimal_swaps", "proj_total",
        "optimal_total", "actual_total"}
# web/lib/api.ts: type WaiverRow = Player & {...}
WAIVER_EXTRA = {"ros_proj", "week_proj", "delta_roster", "delta_pool", "would_start", "week_delta", "add"}
# web/lib/api.ts: type League["standings"][number]
STANDINGS_ROW = {"rank", "team_id", "team", "wins", "losses", "ties", "points_for", "points_against", "me"}
# web/lib/api.ts: type Week / type League top level (stale_since is added by the API's cache)
WEEK = {"week", "live", "me", "opp"}
LEAGUE = {"week", "standings", "scoreboard", "power"}


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
    team = SimpleNamespace(team_id=7, team_name="Mine", wins=2, losses=1, ties=0,
                           points_for=250.5, points_against=210.25)
    match = SimpleNamespace(home_team=team, away_team=SimpleNamespace(team_name="Them"),
                            home_score=110.0, away_score=99.5)
    lg = SimpleNamespace(current_week=3, standings=lambda: [team], scoreboard=lambda wk: [match],
                         power_rankings=lambda wk: [("12.34", team)])
    monkeypatch.setattr(espn_season, "fresh_league", lambda: lg)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    st = espn_season.league_state()
    assert set(st) == LEAGUE
    assert set(st["standings"][0]) == STANDINGS_ROW
    assert set(st["scoreboard"][0]) == {"home", "away", "home_score", "away_score"}
    assert set(st["power"][0]) == {"team", "score"}


def test_week_state_top_level_keys(monkeypatch):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[], away_team=SimpleNamespace(), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "fresh_league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    assert set(espn_season.week_state(week=1, sleeper={}, season={}, injuries={})) == WEEK
