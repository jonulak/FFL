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
    sources.Cached("persist").get(1, lambda: {("a", "RB"): 1.0})
    assert db.get_state("source_status")["persist"]["rows"] == {"RB": 1}


def test_expire_all_forces_a_refetch(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    c = sources.Cached("exp")
    c.get(1, lambda: {("a", "RB"): 1.0})
    sources.expire_all()
    assert c.get(1, lambda: {("b", "RB"): 2.0}) == {("b", "RB"): 2.0}
