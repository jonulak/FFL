"""The flush: quiet hours, grouping, prefs, delivery. Senders are fakes; nothing reaches a push service."""
from datetime import datetime, timedelta

from ffl import db, notify

T = datetime.fromisoformat("2026-10-09T10:00:00-04:00")


def _n(key, kind="move", body="Bears +X", age=timedelta(0)):
    return {"key": key, "kind": kind, "title": "t", "body": body, "url": "/transactions",
            "created_at": (T - age).isoformat(timespec="seconds")}


class Gone(Exception):
    def __init__(self, status):
        self.response = type("R", (), {"status_code": status})()


def test_quiet_window_wraps_midnight_and_equal_means_none():
    q = ["23:00", "07:00"]
    at = lambda hm: T.replace(hour=int(hm[:2]), minute=int(hm[3:]))
    assert notify.in_quiet(at("23:30"), q) and notify.in_quiet(at("06:59"), q)
    assert not notify.in_quiet(at("07:00"), q) and not notify.in_quiet(at("22:59"), q)
    assert notify.in_quiet(at("13:00"), ["12:00", "14:00"]) and not notify.in_quiet(at("03:00"), ["07:00", "07:00"])


def test_moves_collapse_trades_and_injuries_go_alone():
    unsent = [_n("move:1"), _n("move:2", body="Lions −Y"), _n("move:3", "trade", "Bears get Z"),
              _n("injury:5:9:OUT", "injury", "Starting at RB.")]
    pushes, marks = notify.plan(unsent, notify.DEFAULT_PREFS, T)
    assert marks == {"off": [], "expired": []}
    assert [(p["title"], p["body"], p["tag"], p["keys"]) for p in pushes] == [
        ("2 league moves", "Bears +X; Lions −Y", "moves", ["move:1", "move:2"]),
        ("Trade", "Bears get Z", "trade", ["move:3"]),
        ("t", "Starting at RB.", "injury:5:9:OUT", ["injury:5:9:OUT"]),
    ]


def test_one_move_reads_singular():
    pushes, _ = notify.plan([_n("move:1")], notify.DEFAULT_PREFS, T)
    assert pushes[0]["title"] == "1 league move"


def test_a_big_run_is_one_push_cut_to_180_chars():
    unsent = [_n(f"move:{i}", body=f"Team {i} +Somebody Longname") for i in range(20)]
    (push,), _ = notify.plan(unsent, notify.DEFAULT_PREFS, T)
    assert push["title"] == "20 league moves" and len(push["body"]) <= 180
    assert push["body"].endswith("more") and "… +" in push["body"] and len(push["keys"]) == 20


def test_switched_off_and_stale_are_marked_not_sent():
    prefs = {**notify.DEFAULT_PREFS, "move": False}
    unsent = [_n("move:1"), _n("move:2", "trade"), _n("injury:5:9:OUT", "injury", age=timedelta(hours=25))]
    pushes, marks = notify.plan(unsent, prefs, T)
    assert pushes == [] and marks == {"off": ["move:1", "move:2"], "expired": ["injury:5:9:OUT"]}


def test_flush_holds_in_quiet_hours_and_sends_after():
    db.add_notices([_n("move:1")])
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"})
    sent = []
    held = notify.flush(T.replace(hour=2), send=lambda sub, payload: sent.append(payload))
    assert held == {"held": 1} and sent == [] and len(db.unsent_notices()) == 1
    out = notify.flush(T.replace(hour=7, minute=0), send=lambda sub, payload: sent.append(payload))
    assert out["sent"] == 1 and sent[0]["url"] == "/transactions" and db.unsent_notices() == []


def test_a_dead_subscription_beside_a_live_one_still_counts_as_sent():
    db.add_notices([_n("move:1")])
    db.put_subscription("https://push/dead", {"p256dh": "k", "auth": "a"})
    db.put_subscription("https://push/live", {"p256dh": "k", "auth": "a"})

    def send(sub, payload):
        if sub["endpoint"].endswith("dead"):
            raise Gone(410)
    out = notify.flush(T, send=send)
    assert out["sent"] == 1 and [s["endpoint"] for s in db.subscriptions()] == ["https://push/live"]
    assert db.recent_notices()[0]["outcome"] == "sent"


def test_a_network_error_leaves_the_notice_queued():
    db.add_notices([_n("move:1")])
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"})

    def send(sub, payload):
        raise ConnectionError("no network")
    out = notify.flush(T, send=send)
    assert out["sent"] == 0 and len(db.unsent_notices()) == 1 and len(db.subscriptions()) == 1


def test_no_subscription_leaves_the_queue_alone():
    db.add_notices([_n("move:1")])
    assert notify.flush(T, send=lambda s, p: None)["sent"] == 0 and len(db.unsent_notices()) == 1


def test_disabled_flush_sends_nothing(monkeypatch):
    db.add_notices([_n("move:1")])
    assert notify.flush(T) == {"disabled": True} and len(db.unsent_notices()) == 1   # FFL_NOTIFY=0 in conftest


def test_prefs_merge_over_defaults_and_reject_bad_times():
    assert notify.prefs() == notify.DEFAULT_PREFS
    assert notify.put_prefs({"injury": False})["injury"] is False and notify.prefs()["move"] is True
    import pytest
    with pytest.raises(ValueError):
        notify.put_prefs({"quiet": ["25:00", "07:00"]})


def test_vapid_keys_are_what_pywebpush_reads():
    from py_vapid import Vapid01
    pub, priv = notify.vapid_keys()
    assert len(pub) == 87 and Vapid01.from_string(priv).private_key is not None
