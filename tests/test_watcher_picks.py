"""Where the watcher's pick log and state go: the DB, under the watcher's own league; a practice run's DB is its
data dir's ffl.db, and nothing is written beside it."""
import shutil

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
    assert sorted(p.name for p in mock.iterdir()) == ["ffl.db", "ffl.db-shm", "ffl.db-wal"]
    assert not list(tmp_path.rglob("*.csv")) and not list(tmp_path.rglob("*.json"))
    assert [r["overall"] for r in db.picks()] == [1, 2]
    assert db.draft_state(leagues.home())["picks_made"] == 2
