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
    clock[0] += sources.RETRY_S + 1
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


def test_expire_all_keeps_a_long_ttl_entry(monkeypatch):
    """A far week is fetched with a long TTL because it barely moves; Refresh must not re-scrape every one of them."""
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    c = sources.Cached("exp-long")
    c.get(9, lambda: {("a", "RB"): 1.0}, ttl=6 * 3600)
    sources.expire_all()
    assert c.get(9, lambda: pytest.fail("kept through the refresh")) == {("a", "RB"): 1.0}


def test_check_per_call_minimum_and_empty_always_fails():
    with pytest.raises(sources.Thin, match="empty"):
        sources.check({}, {})
    assert sources.check({("a", "RB"): 1.0}, {}) == {"RB": 1}


def test_cached_get_with_min_rows_accepts_what_the_default_rejects():
    one = {("a", "RB"): 1.0}
    assert sources.Cached("t-default").get(1, lambda: one) == {}
    assert sources.Cached("t-loose").get(1, lambda: one, min_rows={}) == one


def test_a_failure_cools_down_then_retries(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "time", lambda: clock[0])
    c = sources.Cached("cool")
    good = {("a", "RB"): 5.0}
    calls = []

    def down():
        calls.append(1)
        raise requests.ConnectionError("down")
    assert c.get(1, down) == {} and c.get(1, down) == {} and len(calls) == 1, "nothing good: {}, asked once"
    clock[0] += sources.RETRY_S + 1
    assert c.get(1, lambda: good) == good
    clock[0] += sources.TTL_S + 1
    assert c.get(1, down) == good and c.get(1, down) == good and len(calls) == 2, "last good through the cooldown"
    clock[0] += sources.RETRY_S + 1
    c.get(1, down)
    assert len(calls) == 3


def test_idle_is_a_success_with_nothing_and_skips_the_check():
    c = sources.Cached("idle")
    sources._failed("idle", RuntimeError("old"))

    def idle():
        raise sources.Idle("week 4 shown")
    assert c.get(1, idle) == {}
    assert c.get(1, lambda: pytest.fail("cached")) == {}
    assert sources.STATUS["idle"]["failing_since"] is None and sources.STATUS["idle"]["error"] is None


def test_status_snapshot_is_a_copy():
    sources._ok("snap", {"RB": 1})
    snap = sources.status_snapshot()
    snap["snap"]["rows"] = None
    snap["other"] = {}
    assert sources.STATUS["snap"]["rows"] == {"RB": 1} and "other" not in sources.STATUS
