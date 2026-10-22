from ffl import db


def _sig(key, pid=10, at="2026-10-22T10:00:00"):
    return {"key": key, "player_id": pid, "kind": "news", "text": key, "at": at}


def test_watch_roundtrip():
    db.put_watch(10, True)
    db.put_watch(20, True)
    db.put_watch(10, True)
    assert db.watched_ids() == [10, 20]
    db.put_watch(10, False)
    assert db.watched_ids() == [20]


def test_add_watch_signals_returns_only_new():
    a, b, c = _sig("a", at="2026-10-22T10:00:00"), _sig("b", at="2026-10-22T11:00:00"), _sig("c", at="2026-10-22T12:00:00")
    assert db.add_watch_signals([a, b]) == [a, b]
    assert db.add_watch_signals([a, b, c]) == [c]
    assert [r["key"] for r in db.watch_signals("2000")] == ["c", "b", "a"]
    assert db.watch_signals("2026-10-22T11:00:00") == [c, b]


def test_unwatch_keeps_signals():
    db.put_watch(10, True)
    db.add_watch_signals([_sig("a")])
    db.put_watch(10, False)
    assert db.watched_ids() == []
    assert [r["key"] for r in db.watch_signals("2000")] == ["a"]
