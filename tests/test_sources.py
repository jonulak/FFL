"""ffl.sources: the shared cache, thin-scrape guard and health record (no network)."""
import pytest
import requests

from ffl import db, sources


def _rows(n_by_pos):
    return {(f"p{pos}{i}", pos): 1.0 for pos, n in n_by_pos.items() for i in range(n)}


def test_check_counts_rows_and_rejects_a_thin_position():
    assert sources.check(_rows({"QB": 20, "RB": 20, "WR": 20, "TE": 10})) == {"QB": 20, "RB": 20, "WR": 20, "TE": 10}
    with pytest.raises(sources.Thin, match="RB"):
        sources.check(_rows({"QB": 20, "RB": 10, "WR": 20, "TE": 10}))


def test_cached_serves_fresh_then_last_good_then_nothing(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "time", lambda: clock[0])
    c = sources.Cached("test")
    good = {("a", "RB"): 5.0}
    assert c.get(5, lambda: good) == good
    assert c.get(5, lambda: pytest.fail("cached")) == good
    clock[0] += sources.TTL_S + 1

    def down():
        raise requests.ConnectionError("down")
    assert c.get(5, down) == good, "expired and down: last good"
    assert sources.STATUS["test"]["error"] == "down" and sources.STATUS["test"]["failing_since"]
    assert c.get(6, down) == {}, "nothing for that key"
    assert c.get(7, lambda: {}) == {}, "an empty scrape is thin: a failure, not a result"
    assert c.get(5, lambda: good) == good
    assert sources.STATUS["test"]["error"] is None and sources.STATUS["test"]["failing_since"] is None


def test_status_survives_a_restart(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    other = {"ok_at": None, "rows": {}, "error": "down", "failing_since": "2026-10-10T00:00:00-04:00"}
    db.put_state("source_status", {"other": other})
    sources.reset()  # a restart: memory empty, the DB still holds the record
    sources.Cached("persist").get(1, lambda: {("a", "RB"): 1.0})
    saved = db.get_state("source_status")
    assert saved["other"]["failing_since"] == "2026-10-10T00:00:00-04:00"
    assert saved["persist"]["rows"] == {"RB": 1}


def test_a_status_save_failure_does_not_fail_the_fetch(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})

    def boom(*a, **k):
        raise RuntimeError("schema stale")
    monkeypatch.setattr(db, "put_state", boom)
    good = {("a", "RB"): 1.0}
    assert sources.Cached("nosave").get(1, lambda: good) == good


def test_a_parser_surprise_serves_last_good(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "time", lambda: clock[0])
    c = sources.Cached("surprise")
    good = {("a", "RB"): 1.0}
    c.get(1, lambda: good)
    clock[0] += sources.TTL_S + 1

    def surprise():
        raise AttributeError("reshaped page")
    assert c.get(1, surprise) == good
    assert c.get(2, surprise) == {}
    assert "reshaped page" in sources.STATUS["surprise"]["error"]


def test_expire_all_forces_a_refetch(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    c = sources.Cached("exp")
    c.get(1, lambda: {("a", "RB"): 1.0})
    sources.expire_all()
    assert c.get(1, lambda: {("b", "RB"): 2.0}) == {("b", "RB"): 2.0}
