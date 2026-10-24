"""Where the watcher's pick log and state go: the DB, under the watcher's own league; a practice run's DB is its
data dir's ffl.db, and nothing is written beside it."""
import shutil

import sqlalchemy as sa

from ffl import config, db, leagues
from ffl import draft_watch as dw

from .test_watcher_rollout import _watcher


def _picks(players):
    return [{"teamId": t, "playerId": int(players.iloc[i].player_id), "overallPickNumber": i + 1,
             "roundId": 1, "roundPickNumber": i + 1} for i, t in enumerate([1, 2])]


def _other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, False, None, None))


def test_real_draft_picks_go_to_the_db(monkeypatch, tmp_path):
    w, players = _watcher(monkeypatch, tmp_path, None)
    w.tick(_picks(players), on_clock=3)
    got = db.picks()
    assert [r["overall"] for r in got] == [1, 2]
    assert set(got[0]) == set(db._PICK_COLS)
    assert got[0]["team"] == "Team 1" and got[0]["player_id"] == int(players.iloc[0].player_id)
    assert got[0]["proj"] is not None and got[0]["ts"]


def test_watcher_writes_under_its_league(monkeypatch, tmp_path):
    other = _other()
    w, players = _watcher(monkeypatch, tmp_path, None, ref=other)
    w.tick(_picks(players), on_clock=3)
    assert [r["overall"] for r in db.picks(ref=other)] == [1, 2]
    assert db.picks(ref=leagues.home()) == []
    state = db.draft_state(other)
    assert state["picks_made"] == 2 and state["on_clock_is_me"] is True and state["recommend"]["top"]
    assert db.draft_state(leagues.home()) is None


def _busy_once(monkeypatch, name):
    """db.<name> raises the busy-DB error on its first call, then works."""
    real, calls = getattr(db, name), []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise sa.exc.OperationalError("INSERT", {}, Exception("database is locked"))
        return real(*a, **k)
    monkeypatch.setattr(db, name, flaky)
    return calls


def test_a_busy_db_does_not_end_the_watcher(monkeypatch, tmp_path, capsys):
    w, players = _watcher(monkeypatch, tmp_path, None)
    _busy_once(monkeypatch, "put_picks")
    assert w.safe_tick(_picks(players), on_clock=3) is None
    assert "DB error" in capsys.readouterr().out and db.picks() == []
    state = w.safe_tick(_picks(players), on_clock=3)
    assert state["_fresh"], "the picks the failed tick missed are still new, so the board prints for them"
    assert [r["overall"] for r in db.picks()] == [1, 2]


def test_state_is_written_when_it_changes_and_retried_after_a_failure(monkeypatch, tmp_path, capsys):
    w, players = _watcher(monkeypatch, tmp_path, None)
    calls = _busy_once(monkeypatch, "put_draft_state")
    w.tick(_picks(players), on_clock=3)
    assert db.draft_state(leagues.home()) is None and "draft state not saved" in capsys.readouterr().out
    w.tick(_picks(players), on_clock=3)
    assert db.draft_state(leagues.home())["picks_made"] == 2 and len(calls) == 2
    w.tick(_picks(players), on_clock=3)
    assert len(calls) == 2, "an unchanged state (its stamp aside) is not written again"
    w.tick(_picks(players)[:1] + _picks(players)[1:], on_clock=4)
    assert len(calls) == 3 and db.draft_state(leagues.home())["on_clock_is_me"] is False


def test_practice_run_writes_no_csv(monkeypatch, tmp_path, _db_template):
    mock = tmp_path / "data-mock"
    mock.mkdir()
    shutil.copy(_db_template, mock / "ffl.db")
    monkeypatch.setenv("FFL_DATA_DIR", str(mock))
    monkeypatch.setattr(config, "DATA", mock)
    monkeypatch.setattr(db, "DATA", mock)              # what FFL_DATA_DIR sets at import
    monkeypatch.delenv("FFL_DB_URL")
    db.reset()
    assert db.url() == f"sqlite:///{mock / 'ffl.db'}"
    w, players = _watcher(monkeypatch, tmp_path, None)
    w.tick(_picks(players), on_clock=3)
    assert {p.name for p in mock.iterdir()} <= {"ffl.db", "ffl.db-shm", "ffl.db-wal"}, "nothing beside the DB"
    assert not list(tmp_path.rglob("*.csv")) and not list(tmp_path.rglob("*.json"))
    assert [r["overall"] for r in db.picks()] == [1, 2]
    assert db.draft_state(leagues.home())["picks_made"] == 2
