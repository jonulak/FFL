from ffl import db
from tests.home import home_league


def _sig(key, pid=10, at="2026-10-22T10:00:00"):
    return {"key": key, "player_id": pid, "kind": "news", "text": key, "at": at}


def test_watch_roundtrip():
    db.put_watch(10, True, ref=home_league())
    db.put_watch(20, True, ref=home_league())
    db.put_watch(10, True, ref=home_league())
    assert db.watched_ids(ref=home_league()) == [10, 20]
    db.put_watch(10, False, ref=home_league())
    assert db.watched_ids(ref=home_league()) == [20]


def test_add_watch_signals_returns_only_new():
    a, b, c = _sig("a", at="2026-10-22T10:00:00"), _sig("b", at="2026-10-22T11:00:00"), _sig("c", at="2026-10-22T12:00:00")
    assert db.add_watch_signals([a, b], ref=home_league()) == [a, b]
    assert db.add_watch_signals([a, b, c], ref=home_league()) == [c]
    assert [r["key"] for r in db.watch_signals("2000", ref=home_league())] == ["c", "b", "a"]
    assert db.watch_signals("2026-10-22T11:00:00", ref=home_league()) == [c, b]


def test_unwatch_keeps_signals():
    db.put_watch(10, True, ref=home_league())
    db.add_watch_signals([_sig("a")], ref=home_league())
    db.put_watch(10, False, ref=home_league())
    assert db.watched_ids(ref=home_league()) == []
    assert [r["key"] for r in db.watch_signals("2000", ref=home_league())] == ["a"]


def test_add_watch_signals_inserts_in_key_order(monkeypatch):
    """Two writers offering overlapping signals in different orders must lock the keys in one order (sorted), or each
    can hold a key the other waits on: a deadlock. The rows come back in the order offered all the same."""
    seen = []
    real = db._upsert

    def spy(table, rows, keys, update):
        seen.append([r["key"] for r in rows])
        return real(table, rows, keys, update)
    monkeypatch.setattr(db, "_upsert", spy)
    rows = [_sig(k) for k in ("c", "a", "b")]
    assert db.add_watch_signals(rows, ref=home_league()) == rows
    assert seen == [["a", "b", "c"]]
