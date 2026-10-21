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
    both = [{"id": "a", "add": 11, "drop": None, "priority": 1}, {"id": "b", "add": 12, "drop": None, "priority": 2}]
    snaps = iter([both, both, []])
    monkeypatch.setattr(espn_write, "pending_claims", lambda: next(snaps))
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
    monkeypatch.setattr(espn_season, "record_projections", down)
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: None)  # the real failure mode: None, never a raise
    flushed = []
    monkeypatch.setattr(notify, "flush", lambda: flushed.append(1) or {"disabled": True})
    out = notify.tick()
    assert out["moves"] == {"error": "espn down"} and out["injuries"] == {"error": "espn down"}
    assert out["projections"] == {"error": "espn down"} and out["sources"] == 0
    assert out["waivers"] == 0 and flushed == [1]


def _claims(*ids):
    return [{"id": str(i), "add": i, "drop": None, "priority": n} for n, i in enumerate(ids, 1)]


def _adds(*rows):
    return [{"id": f"{ms}-{team}-WAIVER ADDED-{pid}", "date": date, "type": "WAIVER ADDED", "team": "t",
             "player": "p", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""} for ms, team, pid, date in rows]


def test_unreadable_run_leaves_the_snapshot_alone(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", None, "2026-10-08T03:12:00"])
    snaps = iter([_claims(11), _claims(12), _claims(12)])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", lambda: next(snaps))
    assert notify.check_waivers() == 0                      # seeds r1 with claims [11]
    assert notify.check_waivers() == 0                      # unreadable: no state touched
    assert db.get_state("pending_claims") == _claims(11)
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")))
    assert notify.check_waivers() == 1
    assert [n for n in db.unsent_notices() if n["kind"] == "waivers"][0]["body"] == "Won Ray Davis."


def test_new_run_waits_for_the_log_to_sync(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    snaps = iter([_claims(11), _claims(12), _claims(12)])
    monkeypatch.setattr(espn_write, "pending_claims", lambda: next(snaps))
    notify.check_waivers()
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")))
    assert notify.check_waivers(synced=False) == 0
    assert db.get_state("waiver_run") == "2026-10-01T03:10:00" and db.get_state("pending_claims") == _claims(11)
    assert notify.check_waivers(synced=True) == 1
    assert [n for n in db.unsent_notices() if n["kind"] == "waivers"][0]["body"] == "Won Ray Davis."


def test_a_win_from_the_previous_run_is_not_counted_again(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    snaps = iter([_claims(11), []])
    monkeypatch.setattr(espn_write, "pending_claims", lambda: next(snaps))
    notify.check_waivers()
    db.add_transactions(_adds((1790000000000, 7, 11, "2026-10-01T03:10")))   # won last run; claimed again, lost now
    assert notify.check_waivers() == 1
    assert [n for n in db.unsent_notices() if n["kind"] == "waivers"][0]["body"] == "Lost Ray Davis."


def test_a_run_that_leaves_my_claim_pending_sends_nothing(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-02T03:10:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", lambda: _claims(11))
    notify.check_waivers()
    assert notify.check_waivers() == 0                       # a run on another day did not process the claim
    assert [n for n in db.unsent_notices() if n["kind"] == "waivers"] == []
    assert db.get_state("waiver_run") == "2026-10-02T03:10:00" and db.get_state("pending_claims") == _claims(11)


def test_only_the_resolved_claim_is_reported(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    snaps = iter([_claims(11, 12), _claims(12)])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", lambda: next(snaps))
    notify.check_waivers()
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")))
    assert notify.check_waivers() == 1
    (w,) = [n for n in db.unsent_notices() if n["kind"] == "waivers"]
    assert w["title"] == "Waivers: 1 of 1 players won" and w["body"] == "Won Ray Davis."
    assert db.get_state("pending_claims") == _claims(12)


def test_pending_claims_raising_stores_nothing_and_the_next_tick_reports(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00", "2026-10-08T03:12:00"])
    snaps = iter([_claims(11), espn_write.WriteError(503, "espn down"), _claims(12)])

    def pending():
        s = next(snaps)
        if isinstance(s, Exception):
            raise s
        return s
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", pending)
    notify.check_waivers()
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")))
    with pytest.raises(espn_write.WriteError):
        notify.check_waivers()
    assert db.get_state("waiver_run") == "2026-10-01T03:10:00" and db.get_state("pending_claims") == _claims(11)
    assert notify.check_waivers() == 1
    assert [n for n in db.unsent_notices() if n["kind"] == "waivers"][0]["body"] == "Won Ray Davis."


def test_tick_records_projections_and_checks_sources(monkeypatch, lg):
    monkeypatch.setattr(journal, "sync_transactions", lambda lg: [])
    monkeypatch.setattr(notify, "check_injuries", lambda: 0)
    monkeypatch.setattr(notify, "check_waivers", lambda synced=True: 0)
    monkeypatch.setattr(espn_season, "record_projections", lambda: {"week": 3, "ros": 2})
    db.put_state("source_status", {})
    out = notify.tick()
    assert out["projections"] == {"week": 3, "ros": 2} and out["sources"] == 0
