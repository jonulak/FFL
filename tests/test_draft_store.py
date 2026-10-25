"""draft_watch.store_espn_draft: a finished ESPN draft stored under its league (ffl-soc.4.14)."""
from types import SimpleNamespace as NS

import pytest

from ffl import config, db, draft_watch, espn_season, leagues


def _other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, False, None, None))


def _pick(rnd, rp, team_id, pid, name):
    return NS(round_num=rnd, round_pick=rp, team=NS(team_id=team_id, team_name=f"Team {team_id}"),
              playerId=pid, playerName=name)


def _stub(monkeypatch, drafted=True):
    draft = [_pick(1, 1, 3, 11, "Josh Allen"), _pick(1, 2, 4, 12, "Bijan Robinson"), _pick(2, 1, 4, -16001, "Jets D/ST")]
    monkeypatch.setattr(draft_watch, "fetch_picks", lambda ref=None: {"in_progress": False, "drafted": drafted, "picks": []})
    monkeypatch.setattr(config, "league", lambda ref=None, fetch_league=True: NS(draft=draft))
    monkeypatch.setattr(espn_season, "tx_player_info", lambda ids, **k: {
        11: {"pos": "QB", "team": "BUF"}, 12: {"pos": "RB", "team": "ATL"}, -16001: {"pos": "D/ST", "team": "NYJ"}})


def test_stores_the_finished_draft_under_its_league(monkeypatch):
    home, other = leagues.home(), _other()
    _stub(monkeypatch)
    assert draft_watch.store_espn_draft(other) == 3
    rows = db.picks(ref=other)
    assert [r["overall"] for r in rows] == [1, 2, 3]
    assert rows[1] == {"overall": 2, "round": 1, "pick": 2, "team_id": 4, "team": "Team 4", "player_id": 12,
                       "player": "Bijan Robinson", "pos": "RB", "nfl": "ATL", "adp_2qb": None,
                       "board_rank_at_pick": None, "proj": None, "ts": None}
    assert rows[2]["round"] == 2 and rows[2]["pos"] == "D/ST"
    assert db.picks(ref=home) == [], "home's draft is untouched"


def test_refuses_an_undrafted_league(monkeypatch):
    other = _other()
    _stub(monkeypatch, drafted=False)
    with pytest.raises(SystemExit):
        draft_watch.store_espn_draft(other)
    assert db.picks(ref=other) == []


def _no_espn(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ESPN was read")
    monkeypatch.setattr(draft_watch, "fetch_picks", boom)
    monkeypatch.setattr(config, "league", boom)
    monkeypatch.setattr(espn_season, "tx_player_info", boom)


def _watched(team_id):
    return {"overall": 1, "round": 1, "pick": 1, "team_id": team_id, "team": "Me", "player_id": 11,
            "player": "Josh Allen", "pos": "QB", "nfl": "BUF", "adp_2qb": 3.5,
            "board_rank_at_pick": 2, "proj": 301.2, "ts": "2026-08-30T12:00:00"}


def test_refuses_the_home_league_and_keeps_its_watched_picks(monkeypatch):
    home = leagues.home()
    db.put_picks([_watched(7)], ref=home)
    before = db.picks(ref=home)
    _no_espn(monkeypatch)
    with pytest.raises(SystemExit):
        draft_watch.store_espn_draft(home)
    assert db.picks(ref=home) == before


def test_refuses_a_league_that_already_has_picks(monkeypatch):
    other = _other()
    db.put_picks([_watched(3)], ref=other)
    before = db.picks(ref=other)
    _no_espn(monkeypatch)
    with pytest.raises(SystemExit):
        draft_watch.store_espn_draft(other)
    assert db.picks(ref=other) == before
