"""The tick's I/O: each detector reads ESPN through patched seams; one failing step never stops the flush."""
from types import SimpleNamespace

import pytest

from ffl import db, espn_season, espn_write, journal, notify
from tests.home import HOME_KEY, home_league


@pytest.fixture
def lg(monkeypatch):
    league = SimpleNamespace(current_week=5, player_map={11: "Ray Davis", 12: "Tyler Allgeier"})
    monkeypatch.setattr(espn_season, "fresh_league", lambda *a, **k: league)
    monkeypatch.setattr(espn_season, "my_team_id", lambda lg, *a, **k: 7)
    return league


def _stored(ref, team: int, entries: list[tuple[int, int]]) -> None:
    """A stored week-5 box score where `team` holds (player, lineup slot id) entries."""
    side = {"teamId": team, "rosterForCurrentScoringPeriod": {"entries": [
        {"playerId": p, "lineupSlotId": s} for p, s in entries]}}
    db.put_box_doc(5, {"matchups": [{"home": side, "away": {"teamId": 99}}]}, "h", 0.0, ref=ref)


def _mroster(team: int, slot: int) -> dict:
    return {"scoringPeriodId": 5, "teams": [{"id": team, "roster": {"entries": [{"playerId": 9, "lineupSlotId": slot,
            "playerPoolEntry": {"player": {"injuryStatus": "OUT", "fullName": "Isiah Pacheco"}}}]}}]}


def test_injuries_read_only_the_leagues_starting_a_newly_hurt_player(monkeypatch):
    """Three leagues: he starts in home, was benched since the stored read in another, is absent from the third. One
    fresh read each for the first two, none for the third; only home's member hears; a quiet tick reads nothing."""
    from ffl import leagues
    uid = home_league().user_id
    benched = leagues.register(leagues.LeagueRef("espn:998", "espn", "998", 2026, "benched", 3, None, None), user_id=uid)
    absent = leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "absent", 4, None, None), user_id=uid)
    _stored(home_league(), 7, [(9, 2)])
    _stored(benched, 3, [(9, 2)])           # stored as a starter; mRoster below has him on the bench (20)
    _stored(absent, 4, [(5, 2)])
    feed = {9: {"status": "Out"}, 5: {"status": "Questionable"}}
    monkeypatch.setattr(espn_season, "fetch_injuries", lambda: feed)
    reads = []
    docs = {HOME_KEY: _mroster(7, 2), "espn:998": _mroster(3, 20)}
    monkeypatch.setattr(espn_season, "raw_league_get", lambda view, ref: reads.append(ref.key) or docs[ref.key])
    assert notify.check_injuries() == 1
    assert sorted(reads) == [HOME_KEY, "espn:998"]
    assert [n["key"] for n in db.unsent_notices(ref=home_league())] == ["injury:5:9:OUT"]
    assert db.unsent_notices(ref=leagues.get("espn:998")) == []
    reads.clear()
    assert notify.check_injuries() == 0 and reads == [], "nobody newly hurt: no league is read"
    feed[9] = {"status": "Injured Reserve"}
    monkeypatch.setattr(espn_season, "fetch_injuries", lambda: {})
    assert notify.check_injuries() == 0 and reads == [], "the feed down: nothing read, nothing forgotten"
    assert db.get_state("injury_seen") == {"9": "OUT"}


def test_injuries_a_failed_league_read_is_tried_again(monkeypatch):
    _stored(home_league(), 7, [(9, 2)])
    monkeypatch.setattr(espn_season, "fetch_injuries", lambda: {9: {"status": "Doubtful"}})

    def down(view, ref):
        raise ConnectionError("espn down")
    monkeypatch.setattr(espn_season, "raw_league_get", down)
    with pytest.raises(ConnectionError):
        notify.check_injuries()
    monkeypatch.setattr(espn_season, "raw_league_get", lambda view, ref: _mroster(7, 2))
    assert notify.check_injuries() == 1
    assert [n["key"] for n in db.unsent_notices(ref=home_league())] == ["injury:5:9:DOUBTFUL"], "the feed's, not mRoster's"


def test_check_waivers_seeds_then_reports(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    both = [{"id": "a", "add": 11, "drop": None, "priority": 1}, {"id": "b", "add": 12, "drop": None, "priority": 2}]
    snaps = iter([both, both, []])
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: next(snaps))
    assert notify.check_waivers(ref=home_league()) == 0            # first sight: seeds the run and the snapshot
    assert notify.check_waivers(ref=home_league()) == 0            # same run
    db.add_transactions([{"id": "1791000000000-7-WAIVER ADDED-11", "date": "2026-10-08T03:12", "type": "WAIVER ADDED",
                          "team": "Me", "player": "Ray Davis", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""},
                         {"id": "1791000000000-3-WAIVER ADDED-12", "date": "2026-10-08T03:12", "type": "WAIVER ADDED",
                          "team": "Them", "player": "Tyler Allgeier", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""}], ref=home_league())
    assert notify.check_waivers(ref=home_league()) == 1
    w = [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"][0]
    assert w["body"] == "Won Ray Davis. Lost Tyler Allgeier."


def test_tick_runs_every_step_even_when_espn_is_down(monkeypatch, lg):
    def down(*a, **k):
        raise ConnectionError("espn down")
    monkeypatch.setattr(journal, "sync_transactions", down)
    monkeypatch.setattr(espn_season, "raw_league_get", down)
    monkeypatch.setattr(espn_season, "record_projections", down)
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: None)  # the real failure mode: None, never a raise
    monkeypatch.setattr(espn_season, "fetch_injuries", lambda: {})       # the feed down, nothing cached
    flushed = []
    monkeypatch.setattr(notify, "flush", lambda: flushed.append(1) or {"disabled": True})
    out = notify.tick()
    home = out["leagues"][HOME_KEY]
    assert home["moves"] == {"error": "espn down"} and out["injuries"] == 0
    assert home["projections"] == {"error": "espn down"} and out["sources"] == 0
    assert home["waivers"] == 0 and flushed == [1]


def _claims(*ids):
    return [{"id": str(i), "add": i, "drop": None, "priority": n} for n, i in enumerate(ids, 1)]


def _adds(*rows):
    return [{"id": f"{ms}-{team}-WAIVER ADDED-{pid}", "date": date, "type": "WAIVER ADDED", "team": "t",
             "player": "p", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""} for ms, team, pid, date in rows]


def test_unreadable_run_leaves_the_snapshot_alone(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", None, "2026-10-08T03:12:00"])
    snaps = iter([_claims(11), _claims(12), _claims(12)])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: next(snaps))
    assert notify.check_waivers(ref=home_league()) == 0                      # seeds r1 with claims [11]
    assert notify.check_waivers(ref=home_league()) == 0                      # unreadable: no state touched
    assert db.get_league_state("pending_claims", ref=home_league()) == _claims(11)
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")), ref=home_league())
    assert notify.check_waivers(ref=home_league()) == 1
    assert [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"][0]["body"] == "Won Ray Davis."


def test_new_run_waits_for_the_log_to_sync(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    snaps = iter([_claims(11), _claims(12), _claims(12)])
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: next(snaps))
    notify.check_waivers(ref=home_league())
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")), ref=home_league())
    assert notify.check_waivers(synced=False, ref=home_league()) == 0
    assert db.get_league_state("waiver_run", ref=home_league()) == "2026-10-01T03:10:00" and db.get_league_state("pending_claims", ref=home_league()) == _claims(11)
    assert notify.check_waivers(synced=True, ref=home_league()) == 1
    assert [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"][0]["body"] == "Won Ray Davis."


def test_a_win_from_the_previous_run_is_not_counted_again(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    snaps = iter([_claims(11), []])
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: next(snaps))
    notify.check_waivers(ref=home_league())
    db.add_transactions(_adds((1790000000000, 7, 11, "2026-10-01T03:10")), ref=home_league())   # won last run; claimed again, lost now
    assert notify.check_waivers(ref=home_league()) == 1
    assert [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"][0]["body"] == "Lost Ray Davis."


def test_a_run_that_leaves_my_claim_pending_sends_nothing(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-02T03:10:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: _claims(11))
    notify.check_waivers(ref=home_league())
    assert notify.check_waivers(ref=home_league()) == 0                       # a run on another day did not process the claim
    assert [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"] == []
    assert db.get_league_state("waiver_run", ref=home_league()) == "2026-10-02T03:10:00" and db.get_league_state("pending_claims", ref=home_league()) == _claims(11)


def test_only_the_resolved_claim_is_reported(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    snaps = iter([_claims(11, 12), _claims(12)])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: next(snaps))
    notify.check_waivers(ref=home_league())
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")), ref=home_league())
    assert notify.check_waivers(ref=home_league()) == 1
    (w,) = [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"]
    assert w["title"] == "Waivers: 1 of 1 players won" and w["body"] == "Won Ray Davis."
    assert db.get_league_state("pending_claims", ref=home_league()) == _claims(12)


def test_pending_claims_raising_stores_nothing_and_the_next_tick_reports(monkeypatch, lg):
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00", "2026-10-08T03:12:00"])
    snaps = iter([_claims(11), espn_write.WriteError(503, "espn down"), _claims(12)])

    def pending(**k):
        s = next(snaps)
        if isinstance(s, Exception):
            raise s
        return s
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    monkeypatch.setattr(espn_write, "pending_claims", pending)
    notify.check_waivers(ref=home_league())
    db.add_transactions(_adds((1791000000000, 7, 11, "2026-10-08T03:12")), ref=home_league())
    with pytest.raises(espn_write.WriteError):
        notify.check_waivers(ref=home_league())
    assert db.get_league_state("waiver_run", ref=home_league()) == "2026-10-01T03:10:00" and db.get_league_state("pending_claims", ref=home_league()) == _claims(11)
    assert notify.check_waivers(ref=home_league()) == 1
    assert [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "waivers"][0]["body"] == "Won Ray Davis."


def test_tick_records_projections_and_checks_sources(monkeypatch, lg):
    monkeypatch.setattr(journal, "sync_transactions", lambda lg, **k: [])
    monkeypatch.setattr(notify, "check_injuries", lambda **k: 0)
    monkeypatch.setattr(notify, "check_waivers", lambda synced=True, **k: 0)
    monkeypatch.setattr(espn_season, "record_projections", lambda **k: {"week": 3, "ros": 2})
    db.put_state("source_status", {})
    out = notify.tick()
    assert out["leagues"][HOME_KEY]["projections"] == {"week": 3, "ros": 2} and out["sources"] == 0


def test_tick_does_each_leagues_work_once_and_the_global_work_once(monkeypatch, lg):
    from ffl import leagues
    other = leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)
    calls = []
    rec = lambda name, value: (lambda *a, ref=None, **k: calls.append((name, ref)) or value)  # noqa: E731
    monkeypatch.setattr(journal, "sync_transactions", rec("moves", []))
    monkeypatch.setattr(notify, "check_injuries", rec("injuries", 0))
    monkeypatch.setattr(notify, "check_waivers", rec("waivers", 0))
    monkeypatch.setattr(espn_season, "record_projections", rec("projections", {}))
    monkeypatch.setattr(notify, "check_sources", rec("sources", 0))
    monkeypatch.setattr(notify, "flush", rec("flush", {"sent": 0}))
    out = notify.tick()
    for step in ("moves", "waivers", "projections"):
        assert [r for n, r in calls if n == step] == [home_league(), other], step
    assert [n for n, _ in calls].count("sources") == 1 and [n for n, _ in calls].count("flush") == 1
    assert [n for n, _ in calls].count("injuries") == 1 and out["injuries"] == 0
    each = {"moves": 0, "waivers": 0, "projections": {}}
    assert out["leagues"] == {HOME_KEY: each, "espn:999": each}
