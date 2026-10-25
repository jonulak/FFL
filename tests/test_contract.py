"""The wire contract between the Python builders and the TypeScript types in web/lib/api.ts.

There are no Pydantic response models: FastAPI returns the plain dicts these functions build, so
nothing but this file stops a renamed key from reaching the UI as `undefined`. Every key set below
mirrors a type in `web/lib/api.ts` — Player, Side, LineupEntry, Swap, WaiverRow, League.standings —
and the two must change together. No network: the standings row is built from a fake league object.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

from ffl import espn_season, player_card, season
from tests.test_espn_season import bp
from tests.test_season import TWO_QB, P, roster

# web/lib/api.ts: type Player
PLAYER = {"player_id", "name", "pos", "team", "slot", "bye", "injury", "proj", "spread", "n_src", "proj_src", "actual", "game_played", "season_proj",
          "ros_proj", "ros_n", "ros_spread",
          "return_week", "injury_note", "opponent", "kickoff", "home", "exp", "ahead"}
# web/lib/api.ts: type LineupEntry
LINEUP_ENTRY = {"slot", "player_id", "name", "proj"}
# web/lib/api.ts: type Swap
SWAP = {"slot", "out", "in", "gain"}
# web/lib/api.ts: type Side
SIDE = {"team_id", "team", "owner", "wins", "losses", "ties", "abbrev", "logo", "lineup", "bench", "ir", "optimal", "optimal_swaps", "proj_total",
        "optimal_total", "actual_total"}
# web/lib/api.ts: type WaiverRow = Player & {...}
WAIVER_EXTRA = {"ros_proj", "ros_adj", "week_proj", "delta_roster", "delta_pool", "would_start", "week_delta", "add",
               "status", "waiver_date"}
# web/lib/api.ts: type League["standings"][number]
STANDINGS_ROW = {"rank", "team_id", "team", "owner", "abbrev", "logo", "wins", "losses", "ties", "points_for", "points_against",
                 "streak", "me"}
SCORE_SIDE = {"team_id", "team", "owner", "abbrev", "logo", "wins", "losses", "ties", "score", "proj", "me", "starter_ids",
              "starter_teams", "pending_teams", "starters"}
# web/lib/api.ts: type ScoreStarter
SCORE_STARTER = {"player_id", "pos", "team", "proj", "actual", "game_played", "bye", "injury", "kickoff"}
SCORE_ROW = {"home", "away", "live", "final", "win_prob"}
# web/lib/api.ts: type Week / type League top level (stale_since is added by the API's cache)
WEEK = {"week", "live", "mine", "slots", "me", "opp", "win_prob"}
LEAGUE = {"week", "current_week", "weeks", "playoff_teams", "playoff_weeks", "live", "standings", "scoreboard"}


def test_player_keys():
    assert set(espn_season.flatten_box_player(bp())) == PLAYER | {"proj_espn"}  # an input merge_weekly pops


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
    monkeypatch.setattr(espn_season, "load", lambda: {"reg_season_weeks": 14, "playoff_teams": 6,
                                                      "playoff_weeks": [15, 16, 17]})
    st = espn_season.league_state(2)
    assert set(st) == LEAGUE
    assert st["weeks"] == 17 and st["playoff_weeks"] == [15, 16, 17]  # the picker runs through the playoffs
    assert st["week"] == 2 and st["current_week"] == 3 and st["live"] is False
    assert set(st["standings"][0]) == STANDINGS_ROW
    assert st["standings"][0]["streak"] == "W2" and st["standings"][1]["streak"] == "L1"
    assert len(st["scoreboard"]) == 1  # the bye slot is dropped
    assert set(st["scoreboard"][0]) == SCORE_ROW and st["scoreboard"][0]["live"] is False
    assert set(st["scoreboard"][0]["home"]) == SCORE_SIDE and st["scoreboard"][0]["home"]["me"] is True
    # a box score without lineups keeps ESPN's side projection and has no odds to give
    assert st["scoreboard"][0]["away"]["proj"] == 101.0 and st["scoreboard"][0]["win_prob"] is None
    assert ahead_has_no_odds(espn_season.league_state(5))
    ahead = espn_season.league_state(5)  # beyond current_week: schedule pairings, projection 0
    assert ahead["scoreboard"][0]["home"]["abbrev"] == "THEM" and ahead["scoreboard"][0]["home"]["proj"] == 0


def ahead_has_no_odds(st) -> bool:
    return all(m["win_prob"] is None and m["home"]["starters"] == [] for m in st["scoreboard"])


def test_score_starter_keys(monkeypatch):
    team = SimpleNamespace(team_id=7, team_name="Mine", team_abbrev="M", logo_url="", wins=0, losses=0, ties=0,
                           points_for=0.0, points_against=0.0)
    them = SimpleNamespace(team_id=3, team_name="Them", team_abbrev="T", logo_url="", wins=0, losses=0, ties=0,
                           points_for=0.0, points_against=0.0)
    lineup = [SimpleNamespace(**{**vars(bp()), "slot_position": "QB"})]
    match = SimpleNamespace(home_team=team, away_team=them, home_score=0.0, away_score=0.0, home_lineup=lineup,
                            away_lineup=lineup, home_projected=0.0, away_projected=0.0)
    lg = SimpleNamespace(current_week=1, standings=lambda: [team, them], box_scores=lambda wk: [match],
                         scoreboard=lambda wk: [])
    monkeypatch.setattr(espn_season, "fresh_league", lambda: lg)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    monkeypatch.setattr(espn_season, "load", lambda: {"reg_season_weeks": 14, "playoff_teams": 6})
    monkeypatch.setattr(espn_season, "_sleeper_for", lambda week, settings: {})
    row = espn_season.league_state(1)["scoreboard"][0]
    assert set(row["home"]["starters"][0]) == SCORE_STARTER
    assert set(row["win_prob"]) == {"away", "home"}


def test_week_state_top_level_keys(monkeypatch):
    matchup = SimpleNamespace(home_team=SimpleNamespace(team_id=7, team_name="Mine"),
                              home_lineup=[], away_team=SimpleNamespace(), away_lineup=[])
    lg = SimpleNamespace(box_scores=lambda week: [matchup], current_week=1)
    monkeypatch.setattr(espn_season, "fresh_league", lambda: lg)
    monkeypatch.setattr(espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    assert set(espn_season.week_state(week=1, sources={}, season={}, injuries={})) == WEEK


# web/lib/api.ts: type PlayerLog / SeasonLog / WeekLog / SeasonTotals
PLAYER_LOG = {"player_id", "name", "pos", "team", "jersey", "season", "current_week", "seasons", "playoff_weeks"}
SEASON_LOG = {"season", "weeks", "totals"}
WEEK_LOG = {"week", "opp", "home", "status", "pts", "summary", "box", "volume"}
SEASON_TOTALS = {"games", "pts", "ppg", "summary", "box"}
FX = Path(__file__).parent / "fixtures"


def _allen() -> dict:
    card = json.loads((FX / "espn_playercard.json").read_text())
    return {"players": [p for p in card["players"] if p["player"]["id"] == 3918298]}


def test_player_log_keys():
    scoring = json.loads((FX / "settings_scoring.json").read_text())["scoring"]
    log = player_card.player_log(_allen(), scoring, {2025: json.loads((FX / "pro_schedule_2025.json").read_text())}, 4, 2026)
    assert set(log) == PLAYER_LOG - {"playoff_weeks"}  # espn_season.player_card adds it from the settings
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
    monkeypatch.setattr(espn_season, "opponent_ranks", dict)  # its own test below
    asked = []
    monkeypatch.setattr(espn_season, "position_ranks", lambda pos: asked.append(pos) or {"season": {3918298: 5}, "weeks": {}})
    sleeper_weeks = []

    def sleeper_for(week, settings):
        sleeper_weeks.append(week)
        if week == 6:
            raise RuntimeError("down")  # one week failing costs that week's projection only
        return {("josh allen", "QB"): 20.0 + week}
    monkeypatch.setattr(espn_season, "_sleeper_for", sleeper_for)
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
    assert sorted(past) == list(range(espn_season.FIRST_SEASON, 2025))  # every season ESPN has before last
    assert [s["season"] for s in log["seasons"]] == [2026, 2025, 2024]
    assert sorted(set(sleeper_weeks)) == list(range(4, 19))  # week 4 twice: the page read and the current-week consensus (cached in production)
    assert asked == ["QB"] and log["seasons"][0]["totals"]["pos_rank"] == 5


def test_opponent_ranks_io(monkeypatch):
    """One kona_player_info call, cached; once expired, a failed read serves the last good ranks."""
    calls = []
    doc = {"positionAgainstOpponent": {"positionalRatings": {"1": {"ratingsByOpponent": {"12": {"average": 8.9, "rank": 1}}}}}}

    def fake_get(view, extend="", headers=None, **params):
        calls.append(view)
        if len(calls) > 1:
            raise RuntimeError("down")
        return doc
    monkeypatch.setattr(espn_season, "raw_league_get", fake_get)
    monkeypatch.setattr(espn_season, "_RANKS", [])
    want = {"QB": {"KC": {"rank": 1, "of": 1, "allowed": 8.9}}}
    assert espn_season.opponent_ranks() == want and espn_season.opponent_ranks() == want
    assert calls == ["kona_player_info"]
    espn_season._RANKS[0] = 0  # expired
    assert espn_season.opponent_ranks() == want and len(calls) == 2


def test_position_ranks_io(monkeypatch):
    """One kona_player_info list of the position's slot with every week's rows, cached per
    position; once expired, a failed read serves the last good ranks."""
    calls = []
    doc = {"players": [{"player": {"id": 7, "defaultPositionId": 3, "stats": [
        {"seasonId": espn_season.SEASON, "statSourceId": 0, "statSplitTypeId": 1, "scoringPeriodId": 1,
         "appliedTotal": 12.0, "stats": {"210": 1}}]}}]}

    def fake_get(view, extend="", headers=None, **params):
        calls.append((view, json.loads(headers["x-fantasy-filter"])["players"]))
        if len(calls) > 1:
            raise RuntimeError("down")
        return doc
    monkeypatch.setattr(espn_season, "raw_league_get", fake_get)
    monkeypatch.setattr(espn_season, "_POS_RANKS", {})
    want = {"season": {7: 1}, "weeks": {1: {7: 1}}}
    assert espn_season.position_ranks("WR") == want and espn_season.position_ranks("WR") == want
    view, filt = calls[0]
    assert len(calls) == 1 and view == "kona_player_info" and filt["filterSlotIds"]["value"] == [4]
    espn_season._POS_RANKS["WR"] = (0, want)  # expired
    assert espn_season.position_ranks("WR") == want and len(calls) == 2


# web/lib/api.ts: type Claims (GET/POST /claims; the web type lands with the claims UI)
CLAIMS = {"week", "groups", "now", "claims", "errors", "warnings", "open_spots", "position_caps", "status", "pending", "pending_error",
          "last_submit", "edited_since_submit", "on_espn", "writes", "players"}


def test_claims_keys(monkeypatch):
    from fastapi.testclient import TestClient

    from ffl import api

    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    pool = {"WR": [{"player_id": 101, "name": "Addison", "status": "WAIVERS"}]}
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: {"week": 6, "roster": roster, "positions": pool})
    monkeypatch.setattr(api, "_roster_size", lambda: 17)
    monkeypatch.setattr(api, "_limits", lambda: {})
    api.cache.clear()
    assert set(TestClient(api.app).get("/claims").json()) == CLAIMS


# web/lib/api.ts: type Decision / type Decisions (GET /decisions; POST /decisions/{id} answers one Decision)
DECISION = {"id", "at", "week", "kind", "source", "title", "decision", "facts", "rationale", "rationale_note",
            "followed_call", "alternatives", "exit_conditions", "outcome", "outcome_at", "body", "player_ids"}
DECISIONS = {"decisions", "weeks"}


def test_decision_keys():
    from fastapi.testclient import TestClient

    from ffl import api, db

    i = db.add_decision({"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app",
                         "title": "Week 6 lineup: A to FLEX", "decision": "A (WR) BE → FLEX", "rationale": None})
    client = TestClient(api.app)
    body = client.get("/decisions").json()
    assert set(body) == DECISIONS and set(body["decisions"][0]) == DECISION
    assert set(client.post(f"/decisions/{i}", json={"outcome": "won"}).json()) == DECISION
    # the TypeScript type, read from the file, so the set above cannot drift from it either
    ts = (Path(__file__).parent.parent / "web" / "lib" / "api.ts").read_text()
    block = ts[ts.index("export type Decision = {"):]
    assert set(re.findall(r"(\w+):", block[:block.index("};")])) == DECISION
