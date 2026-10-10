"""The tick's I/O: each detector reads ESPN through patched seams; one failing step never stops the flush."""
from types import SimpleNamespace

import pytest

from ffl import db, espn_season, espn_write, journal, notify


@pytest.fixture
def lg(monkeypatch):
    league = SimpleNamespace(current_week=5, player_map={11: "Ray Davis", 12: "Tyler Allgeier"})
    monkeypatch.setattr(espn_season, "fresh_league", lambda: league)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg: 7)
    return league


def test_check_injuries_reads_my_roster(monkeypatch, lg):
    doc = {"scoringPeriodId": 5, "teams": [{"id": 7, "roster": {"entries": [
        {"playerId": 9, "lineupSlotId": 2, "playerPoolEntry": {"player": {"injuryStatus": "OUT", "fullName": "Isiah Pacheco"}}}]}}]}
    monkeypatch.setattr(espn_season, "raw_league_get", lambda view, **k: doc)
    assert notify.check_injuries() == 1 and notify.check_injuries() == 0
    assert db.unsent_notices()[0]["key"] == "injury:5:9:OUT"


def test_check_waivers_seeds_then_reports(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims",
                        lambda: [{"id": "a", "add": 11, "drop": None, "priority": 1}, {"id": "b", "add": 12, "drop": None, "priority": 2}])
    assert notify.check_waivers() == 0            # first sight: seeds the run and the snapshot
    assert notify.check_waivers() == 0            # same run
    db.add_transactions([{"id": "1791000000000-7-WAIVER ADDED-11", "date": "2026-10-08T03:12", "type": "WAIVER ADDED",
                          "team": "Me", "player": "Ray Davis", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""},
                         {"id": "1791000000000-3-WAIVER ADDED-12", "date": "2026-10-08T03:12", "type": "WAIVER ADDED",
                          "team": "Them", "player": "Tyler Allgeier", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""}])
    assert notify.check_waivers() == 1
    w = [n for n in db.unsent_notices() if n["kind"] == "waivers"][0]
    assert w["body"] == "Won Ray Davis. Lost Tyler Allgeier."


def test_tick_runs_every_step_even_when_espn_is_down(monkeypatch, lg):
    def down(*a, **k):
        raise ConnectionError("espn down")
    monkeypatch.setattr(journal, "sync_transactions", down)
    monkeypatch.setattr(espn_season, "raw_league_get", down)
    monkeypatch.setattr(espn_write, "last_waiver_run", down)
    flushed = []
    monkeypatch.setattr(notify, "flush", lambda: flushed.append(1) or {"disabled": True})
    out = notify.tick()
    assert out["moves"] == {"error": "espn down"} and out["injuries"] == {"error": "espn down"}
    assert out["waivers"] == {"error": "espn down"} and flushed == [1]
