"""Where the watcher's pick log and state go: the DB, under the watcher's own league; a practice run's DB is its
own database, and nothing is written to its data dir."""
import sqlalchemy as sa

from ffl import config, db, leagues
from ffl import draft_watch as dw

from .test_watcher_rollout import _watcher
from tests.home import home_league


def _picks(players):
    return [{"teamId": t, "playerId": int(players.iloc[i].player_id), "overallPickNumber": i + 1,
             "roundId": 1, "roundPickNumber": i + 1} for i, t in enumerate([1, 2])]


def _other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)


def test_real_draft_picks_go_to_the_db(monkeypatch, tmp_path):
    w, players = _watcher(monkeypatch, tmp_path, None)
    w.tick(_picks(players), on_clock=3)
    got = db.picks(ref=home_league())
    assert [r["overall"] for r in got] == [1, 2]
    assert set(got[0]) == set(db._PICK_COLS)
    assert got[0]["team"] == "Team 1" and got[0]["player_id"] == int(players.iloc[0].player_id)
    assert got[0]["proj"] is not None and got[0]["ts"]


def test_watcher_writes_under_its_league(monkeypatch, tmp_path):
    other = _other()
    w, players = _watcher(monkeypatch, tmp_path, None, ref=other)
    w.tick(_picks(players), on_clock=3)
    assert [r["overall"] for r in db.picks(ref=other)] == [1, 2]
    assert db.picks(ref=home_league()) == []
    state = db.draft_state(other)
    assert state["picks_made"] == 2 and state["on_clock_is_me"] is True and state["recommend"]["top"]
    assert db.draft_state(home_league()) is None


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
    assert "DB error" in capsys.readouterr().out and db.picks(ref=home_league()) == []
    state = w.safe_tick(_picks(players), on_clock=3)
    assert state["_fresh"], "the picks the failed tick missed are still new, so the board prints for them"
    assert [r["overall"] for r in db.picks(ref=home_league())] == [1, 2]


def test_state_is_written_when_it_changes_and_retried_after_a_failure(monkeypatch, tmp_path, capsys):
    w, players = _watcher(monkeypatch, tmp_path, None)
    calls = _busy_once(monkeypatch, "put_draft_state")
    w.tick(_picks(players), on_clock=3)
    assert db.draft_state(home_league()) is None and "draft state not saved" in capsys.readouterr().out
    w.tick(_picks(players), on_clock=3)
    assert db.draft_state(home_league())["picks_made"] == 2 and len(calls) == 2
    w.tick(_picks(players), on_clock=3)
    assert len(calls) == 2, "an unchanged state (its stamp aside) is not written again"
    w.tick(_picks(players)[:1] + _picks(players)[1:], on_clock=4)
    assert len(calls) == 3 and db.draft_state(home_league())["on_clock_is_me"] is False


def test_practice_run_writes_no_csv(monkeypatch, tmp_path, pg_server, _db_template):
    mock = tmp_path / "data-mock"
    mock.mkdir()
    monkeypatch.setenv("FFL_DATA_DIR", str(mock))
    monkeypatch.setattr(config, "DATA", mock)
    monkeypatch.setattr(db, "DATA", mock)              # what FFL_DATA_DIR sets at import
    name = pg_server.fresh_name()                      # the practice run's own database
    monkeypatch.setenv("FFL_DB_URL", pg_server.create(name, template=_db_template))
    db.reset()
    try:
        w, players = _watcher(monkeypatch, tmp_path, None)
        w.tick(_picks(players), on_clock=3)
        assert not list(mock.iterdir()), "nothing in the data dir"
        assert not list(tmp_path.rglob("*.csv")) and not list(tmp_path.rglob("*.json"))
        assert [r["overall"] for r in db.picks(ref=home_league())] == [1, 2]
        assert db.draft_state(home_league())["picks_made"] == 2
    finally:
        db.reset()
        pg_server.drop(name)


def test_state_with_nan_is_saved_as_null(monkeypatch, tmp_path):
    """The candidate table carries NaN (a missing ADP, a one-source spread); Postgres json refuses the token."""
    w, _ = _watcher(monkeypatch, tmp_path, None)
    w.save_state({"picks_made": 1, "top": [{"adp": float("nan"), "spread": float("inf")}]})
    assert db.draft_state(w.ref)["top"] == [{"adp": None, "spread": None}]
