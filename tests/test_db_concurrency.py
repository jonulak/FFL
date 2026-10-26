"""Concurrent writers on Postgres: READ COMMITTED does not serialize a read-modify-write the way SQLite's single
writer did, so a get then a put on one key can lose an update. db.update_state / update_league_state lock the row."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from ffl import db, replay

N = 200


def test_update_league_state_two_threads_lose_nothing():
    def append(start):
        for i in range(start, start + N):
            db.update_league_state("pregame_pulls", lambda v, i=i: [*v, i], [])

    with ThreadPoolExecutor(2) as ex:
        for f in [ex.submit(append, 0), ex.submit(append, 10_000)]:
            f.result()  # re-raises a thread's error
    got = db.get_league_state("pregame_pulls")
    assert len(got) == 2 * N
    assert set(got) == set(range(N)) | set(range(10_000, 10_000 + N))


def test_update_state_two_threads_lose_nothing():
    def append(start):
        for i in range(start, start + N):
            db.update_state("news_mutes", lambda v, i=i: [*v, i], [])

    with ThreadPoolExecutor(2) as ex:
        for f in [ex.submit(append, 0), ex.submit(append, 10_000)]:
            f.result()
    assert set(db.get_state("news_mutes")) == set(range(N)) | set(range(10_000, 10_000 + N))


def test_update_state_returns_the_new_value_and_starts_from_default():
    assert db.update_state("news_mutes", lambda v: [*v, 1], []) == [1]
    assert db.update_league_state("watch_state", lambda v: {**v, "a": 1}, {}) == {"a": 1}
    assert db.get_league_state("watch_state") == {"a": 1}


def test_update_state_rolls_back_when_fn_raises():
    db.put_state("news_mutes", [1])

    def bad(v):
        raise ValueError("no")
    with pytest.raises(ValueError):
        db.update_state("news_mutes", bad, [])
    assert db.get_state("news_mutes") == [1]


def test_update_state_checks_the_key_kind():
    with pytest.raises(ValueError):
        db.update_state("pregame_pulls", lambda v: v)
    with pytest.raises(ValueError):
        db.update_league_state("news_mutes", lambda v: v)


@pytest.mark.xfail(strict=True, reason="the hazard update_*_state exists for: a get then a put loses an update")
def test_naive_get_put_loses_updates():
    db.put_league_state("pregame_pulls", [])
    gate = threading.Barrier(2)

    def add(item):
        cur = db.get_league_state("pregame_pulls", [])
        gate.wait(timeout=10)  # both have read before either writes
        db.put_league_state("pregame_pulls", [*cur, item])

    with ThreadPoolExecutor(2) as ex:
        for f in [ex.submit(add, "a"), ex.submit(add, "b")]:
            f.result()
    assert sorted(db.get_league_state("pregame_pulls")) == ["a", "b"]


def test_add_watch_signals_two_threads_report_each_new_signal_once():
    """Both threads offer the same signals: each must be reported new by exactly one (one push, not two)."""
    gate = threading.Barrier(2)

    def add(rows):
        gate.wait(timeout=10)
        return db.add_watch_signals(rows)

    for round in range(20):  # a race: many rounds, so it shows every run, not one in two
        rows = [{"key": f"k{round}.{i}", "player_id": i, "kind": "usage", "text": "t", "at": "2026-10-26T10:00:00"}
                for i in range(20)]
        with ThreadPoolExecutor(2) as ex:
            a, b = [f.result() for f in [ex.submit(add, rows), ex.submit(add, rows)]]
        assert sorted(r["key"] for r in a + b) == sorted(r["key"] for r in rows)


def test_put_box_doc_first_reads_race_without_error():
    """Two first reads of a week: both used to see no row and both INSERT (a key violation for one)."""
    gate = threading.Barrier(2)

    def put(week, h):
        gate.wait(timeout=10)
        return db.put_box_doc(week, {"h": h}, h, 1.0)

    for week in range(1, 31):
        with ThreadPoolExecutor(2) as ex:
            wrote = [f.result() for f in [ex.submit(put, week, "x"), ex.submit(put, week, "y")]]
        assert wrote[0] or wrote[1]


def test_nan_in_any_json_column_is_stored_as_null():
    db.put_state("news_last_run", {"a": float("nan"), "b": [1, float("inf")], "c": {"d": float("-inf")}, "é": "ü"})
    assert db.get_state("news_last_run") == {"a": None, "b": [1, None], "c": {"d": None}, "é": "ü"}
    assert list(db.get_state("news_last_run")) == ["a", "b", "c", "é"]


def test_read_only_engine_refuses_a_non_postgres_url():
    with pytest.raises(db.SchemaError, match="postgresql\\+psycopg"):
        db._read_only_engine("sqlite:///x.db")


def test_replay_keep_from_a_non_postgres_url_exits_cleanly():
    with pytest.raises(SystemExit, match="postgresql\\+psycopg"):
        replay.keep("x", "sqlite:///x.db", "espn:100000001")
