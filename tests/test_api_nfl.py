import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ffl import api, nfl

FIX = Path(__file__).parent / "fixtures" / "nfl"
SB = json.loads((FIX / "scoreboard-w5.json").read_text())
LIVE = json.loads((FIX / "drives-live-401872990.json").read_text())

ME = [{"player_id": 4429023, "name": "MarShawn Lloyd", "pos": "RB", "slot": "RB", "team": "GB", "actual": 6.0,
       "stat_line": "3 car, 12 yds", "game_played": 0},
      {"player_id": -16003, "name": "Bears D/ST", "pos": "D/ST", "slot": "D/ST", "team": "CHI", "actual": 1.0,
       "stat_line": None, "game_played": 0},
      {"player_id": 4040715, "name": "Jalen Hurts", "pos": "QB", "slot": "QB", "team": "PHI", "actual": 20.0,
       "stat_line": None, "game_played": 100}]
OPP = [{"player_id": 4361741, "name": "Brock Purdy", "pos": "QB", "slot": "QB", "team": "SF", "actual": 0.0,
        "stat_line": None, "game_played": 0}]


@pytest.fixture
def c(monkeypatch):
    nfl.reset()
    def get(url, params=None):
        return SB if "scoreboard" in url else LIVE
    monkeypatch.setattr(nfl, "_get", get)
    monkeypatch.setattr(api, "_nfl_sides", lambda week: (ME, OPP))
    monkeypatch.setattr(api, "_nfl_starters", lambda week: (ME, OPP))
    yield TestClient(api.app)
    nfl.reset()


def test_scoreboard_route(c):
    r = c.get("/nfl").json()
    assert r["week"] == 5 and r["current"] == 5 and r["weeks"] == 18
    assert r["live"] is True and r["sections"][0]["live"] is True
    games = [g for s in r["sections"] for g in s["games"]]
    chi_gb = next(g for g in games if g["home"]["abbr"] == "GB")
    assert chi_gb["mine"] == 2  # Lloyd (GB) and the Bears D/ST
    assert r["byes"] == ["CAR", "KC"]


def test_game_route_marks_and_starters(c):
    r = c.get("/nfl/5/gb").json()
    assert r["game"]["home"]["abbr"] == "GB" and r["live"] is True
    assert [s["name"] for s in r["starters"]["me"]] == ["MarShawn Lloyd", "Bears D/ST"]
    assert r["starters"]["opp"] == []
    plays = [p for d in r["drives"] for p in d["plays"]]
    assert any("MarShawn Lloyd" in p["mine"] for p in plays)
    assert all(p["scoring"] for p in r["scoring"])


def test_game_route_resolves_either_club(c):
    assert c.get("/nfl/5/CHI").json()["game"]["event_id"] == c.get("/nfl/5/GB").json()["game"]["event_id"]


@pytest.mark.parametrize("team", ["KC", "ZZZ"])  # a bye, and junk
def test_game_route_404(c, team):
    assert c.get(f"/nfl/5/{team}").status_code == 404


def test_junk_club_404s_without_a_fetch(monkeypatch):
    nfl.reset()
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: (_ for _ in ()).throw(AssertionError("fetched")))
    assert TestClient(api.app).get("/nfl/5/ZZZ").status_code == 404


def test_current_week_fetch_failure_falls_back(monkeypatch, c):
    def get(url, params=None):
        if params is None:
            raise RuntimeError("down")
        return SB
    nfl.reset()
    monkeypatch.setattr(nfl, "_get", get)
    r = c.get("/nfl?week=5")
    assert r.status_code == 200 and r.json()["current"] == 5


def _soon_sb(minutes):
    sb = json.loads(json.dumps(SB))
    for e in sb["events"]:
        e["status"]["type"]["state"] = "pre"
        e["date"] = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%MZ")
    return sb


@pytest.mark.parametrize("minutes,live", [(10, True), (120, False)])
def test_pregame_pages_poll_from_fifteen_minutes_out(monkeypatch, c, minutes, live):
    sb = _soon_sb(minutes)
    nfl.reset()
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: sb if "scoreboard" in url else {"items": []})
    assert c.get("/nfl").json()["live"] is live
    assert c.get("/nfl/5/gb").json()["live"] is live


def test_cold_outage_is_502(monkeypatch):
    nfl.reset()
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(api, "_nfl_starters", lambda week: ([], []))
    r = TestClient(api.app).get("/nfl")
    assert r.status_code == 502 and r.json()["detail"]["source"] == "espn-nfl"


def test_sides_degrade_to_empty_when_board_fails(monkeypatch):
    def boom(n, team):
        raise api.HTTPException(502, {"source": "espn", "error": "down"})
    monkeypatch.setattr(api, "_board", boom)
    assert api._nfl_sides(5) is None  # unknown, not "no starters": the game page says so


def test_game_route_says_when_marks_are_off(monkeypatch, c):
    assert c.get("/nfl/5/gb").json()["marked"] is True
    monkeypatch.setattr(api, "_nfl_sides", lambda week: None)
    r = c.get("/nfl/5/gb").json()
    assert r["marked"] is False and r["starters"] == {"me": [], "opp": []}
    assert all(p["mine"] == [] for d in r["drives"] for p in d["plays"])


def test_game_route_marked_with_none_of_mine_in_the_game(monkeypatch, c):
    monkeypatch.setattr(api, "_nfl_sides", lambda week: ([], []))
    r = c.get("/nfl/5/gb").json()
    assert r["marked"] is True and r["starters"]["me"] == []


def _no_week_sb():
    sb = json.loads(json.dumps(SB))
    del sb["week"]["number"]
    return sb


FEED = {"GB": {"status": "in", "quarter": 4, "clock": "0:30", "score": {"GB": 99, "CHI": 98}}}


@pytest.mark.parametrize("feed_week,overlaid", [(4, False), (5, True)])
def test_scoreboard_without_espns_week_gates_the_feed_on_the_week_asked(monkeypatch, c, feed_week, overlaid):
    sb = _no_week_sb()
    nfl.reset()
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: sb)
    monkeypatch.setattr(api.live.STATE, "games", FEED)
    monkeypatch.setattr(api.live.STATE, "week", feed_week)
    asked = []
    monkeypatch.setattr(api, "_nfl_starters", lambda week: asked.append(week) or (ME, OPP))
    r = c.get("/nfl?week=5").json()
    gb = next(g for s in r["sections"] for g in s["games"] if g["home"]["abbr"] == "GB")
    assert r["week"] == 5 and asked == [5]
    assert (gb["home"]["score"] == 99) is overlaid, "last week's feed must not land on this week's games"


def test_current_scoreboard_without_espns_week_lays_no_feed(monkeypatch, c):
    sb = _no_week_sb()
    nfl.reset()
    monkeypatch.setattr(nfl, "_get", lambda url, params=None: sb)
    monkeypatch.setattr(api.live.STATE, "games", FEED)
    monkeypatch.setattr(api.live.STATE, "week", 4)
    monkeypatch.setattr(api, "_nfl_starters", lambda week: pytest.fail("no week known to mark"))
    r = c.get("/nfl").json()
    gb = next(g for s in r["sections"] for g in s["games"] if g["home"]["abbr"] == "GB")
    assert r["week"] is None and gb["home"]["score"] != 99


def test_scoreboard_marks_of_another_week_come_off_the_box_scores_not_a_priced_board(monkeypatch):
    priced = []
    def board(n, team):
        priced.append(n)
        return {"week": 6, "me": {"lineup": ME}, "opp": {"lineup": OPP}}
    monkeypatch.setattr(api, "_board", board)
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: None)
    monkeypatch.setattr(api.espn_season, "my_team_id", lambda lg: 7)
    side = lambda tid, ps: {"team_id": tid, "lineup": ps}  # noqa: E731
    monkeypatch.setattr(api.espn_season, "league_starters", lambda week: {"week": week, "games": [
        [side(1, []), side(2, OPP)], [side(9, OPP), side(7, ME)]]})
    assert api._nfl_starters(2) == (ME, OPP)
    assert priced == [None], "only the cached current board is read, never week 2's (7-10 s cold)"


def test_scoreboard_marks_of_the_boards_week_read_the_board(monkeypatch):
    monkeypatch.setattr(api, "_board", lambda n, team: {"week": 6})
    monkeypatch.setattr(api, "_nfl_sides", lambda week: (ME, OPP))
    monkeypatch.setattr(api.espn_season, "league_starters", lambda week: pytest.fail("the board has them"))
    assert api._nfl_starters(6) == (ME, OPP)


def test_scoreboard_marks_degrade_to_empty_when_the_box_scores_fail(monkeypatch):
    monkeypatch.setattr(api, "_board", lambda n, team: {"week": 6})
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: (_ for _ in ()).throw(RuntimeError("espn down")))
    assert api._nfl_starters(2) == ([], [])
