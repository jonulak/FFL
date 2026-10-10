"""The notify tables: subscriptions, the notices outbox, notify_state. The autouse fixture gives each test a migrated copy."""
from ffl import db

T = "2026-10-09T10:00:00-04:00"


def _n(key, kind="move", at=T):
    return {"key": key, "kind": kind, "title": "t", "body": "b", "url": "/transactions", "created_at": at}


def test_subscribing_twice_keeps_one_row_with_the_new_keys():
    db.put_subscription("https://push/x", {"p256dh": "a", "auth": "1"}, "iPhone")
    db.put_subscription("https://push/x", {"p256dh": "b", "auth": "2"}, "iPhone")
    subs = db.subscriptions()
    assert len(subs) == 1 and subs[0]["keys"] == {"p256dh": "b", "auth": "2"}


def test_drop_subscription():
    db.put_subscription("https://push/x", {"p256dh": "a", "auth": "1"})
    db.drop_subscription("https://push/x")
    assert db.subscriptions() == []


def test_add_notices_dedupes_on_key():
    assert db.add_notices([_n("move:1"), _n("move:2")]) == 2
    assert db.add_notices([_n("move:2"), _n("move:3")]) == 1
    assert [n["key"] for n in db.unsent_notices()] == ["move:1", "move:2", "move:3"]


def test_marked_notices_leave_the_queue_and_show_in_recent():
    db.add_notices([_n("move:1"), _n("injury:5:9:OUT", "injury", "2026-10-09T11:00:00-04:00")])
    db.mark_notices(["move:1"], "sent", T)
    assert [n["key"] for n in db.unsent_notices()] == ["injury:5:9:OUT"]
    recent = db.recent_notices()
    assert [n["key"] for n in recent] == ["injury:5:9:OUT", "move:1"]
    assert recent[1]["outcome"] == "sent" and recent[1]["sent_at"] == T


def test_state_round_trip_and_default():
    assert db.get_state("prefs", {"move": True}) == {"move": True}
    db.put_state("prefs", {"move": False})
    db.put_state("prefs", {"move": True, "injury": False})
    assert db.get_state("prefs") == {"move": True, "injury": False}
