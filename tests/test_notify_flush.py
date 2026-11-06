"""The flush: quiet hours, grouping, prefs, delivery. Senders are fakes; nothing reaches a push service."""
from datetime import datetime, timedelta

from ffl import appconf, db, notify
from tests.home import HOME_KEY, home_league

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
    db.add_notices([_n("move:1")], ref=home_league())
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)
    sent = []
    held = notify.flush(T.replace(hour=2), send=lambda sub, payload: sent.append(payload))
    assert held["held"] == 1 and held["sent"] == 0 and sent == [] and len(db.unsent_notices(ref=home_league())) == 1
    out = notify.flush(T.replace(hour=7, minute=0), send=lambda sub, payload: sent.append(payload))
    assert out["sent"] == 1 and sent[0]["url"] == f"/transactions?league={HOME_KEY}&seen=moves" and db.unsent_notices(ref=home_league()) == []


def test_payload_is_declarative_with_the_legacy_fields_beside():
    push = {"keys": ["move:1"], "title": "1 league move", "body": "Bears +X", "url": "/transactions", "tag": "moves"}
    p = notify.payload_for(push, app_url="https://ffl.example:3300/")
    assert p["web_push"] == 8030
    assert p["notification"] == {"title": "1 league move", "body": "Bears +X", "tag": "moves",
                                 "navigate": "https://ffl.example:3300/transactions"}
    assert {k: p[k] for k in ("title", "body", "url", "tag")} == {k: push[k] for k in ("title", "body", "url", "tag")}


def test_a_dead_subscription_beside_a_live_one_still_counts_as_sent():
    db.add_notices([_n("move:1")], ref=home_league())
    db.put_subscription("https://push/dead", {"p256dh": "k", "auth": "a"}, user_id=0)
    db.put_subscription("https://push/live", {"p256dh": "k", "auth": "a"}, user_id=0)

    def send(sub, payload):
        if sub["endpoint"].endswith("dead"):
            raise Gone(410)
    out = notify.flush(T, send=send)
    assert out["sent"] == 1 and [s["endpoint"] for s in db.subscriptions(user_id=0)] == ["https://push/live"]
    assert db.recent_notices(ref=home_league())[0]["outcome"] == "sent"


def test_a_network_error_leaves_the_notice_queued():
    db.add_notices([_n("move:1")], ref=home_league())
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)

    def send(sub, payload):
        raise ConnectionError("no network")
    out = notify.flush(T, send=send)
    assert out["sent"] == 0 and len(db.unsent_notices(ref=home_league())) == 1 and len(db.subscriptions(user_id=0)) == 1


def test_no_subscription_leaves_the_queue_alone():
    db.add_notices([_n("move:1")], ref=home_league())
    assert notify.flush(T, send=lambda s, p: None)["sent"] == 0 and len(db.unsent_notices(ref=home_league())) == 1


def test_disabled_flush_sends_nothing():
    db.add_notices([_n("move:1")], ref=home_league())
    assert notify.flush(T) == {"disabled": True} and len(db.unsent_notices(ref=home_league())) == 1   # FFL_NOTIFY=0 in conftest


def test_prefs_merge_over_defaults_and_reject_bad_times():
    assert notify.prefs(ref=home_league()) == notify.DEFAULT_PREFS
    assert notify.put_prefs({"injury": False}, ref=home_league())["injury"] is False and notify.prefs(ref=home_league())["move"] is True
    import pytest
    with pytest.raises(ValueError):
        notify.put_prefs({"quiet": ["25:00", "07:00"]}, ref=home_league())


def test_vapid_keys_are_what_pywebpush_reads():
    from py_vapid import Vapid01
    pub, priv = notify.vapid_keys()
    assert len(pub) == 87 and Vapid01.from_string(priv).private_key is not None


def test_a_timeout_leaves_the_notice_queued():
    import requests
    db.add_notices([_n("move:1")], ref=home_league())
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)

    def send(sub, payload):
        raise requests.Timeout("hung")
    out = notify.flush(T, send=send)
    assert out["sent"] == 0 and len(db.unsent_notices(ref=home_league())) == 1 and len(db.subscriptions(user_id=0)) == 1


def test_webpush_sender_passes_a_timeout(monkeypatch):
    import pywebpush
    seen = {}
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: seen.update(kw))
    appconf.put(appconf.VAPID_PRIVATE, notify.vapid_keys()[1])
    notify.webpush_sender({"endpoint": "https://push/a", "keys": {"p256dh": "k", "auth": "a"}}, {"title": "x"})
    assert seen["timeout"] == 10


def test_one_long_body_is_cut_with_an_ellipsis_and_no_more_count():
    (push,), _ = notify.plan([_n("move:1", body="x" * 300)], notify.DEFAULT_PREFS, T)
    assert len(push["body"]) == 180 and push["body"].endswith("…") and "more" not in push["body"]


def test_a_long_waivers_body_is_capped():
    (push,), _ = notify.plan([_n("waivers:1", "waivers", "y" * 300)], notify.DEFAULT_PREFS, T)
    assert len(push["body"]) == 180 and push["body"].endswith("…")


def test_an_unreadable_created_at_expires_instead_of_blocking():
    bad = [_n("move:1"), {**_n("move:2"), "created_at": "2026-10-09T10:00:00"}, {**_n("move:3"), "created_at": "garbage"}]
    pushes, marks = notify.plan(bad, notify.DEFAULT_PREFS, T)
    assert marks["expired"] == ["move:2", "move:3"] and [p["keys"] for p in pushes] == [["move:1"]]


def test_prefs_reject_non_bool_switches():
    import pytest
    with pytest.raises(ValueError):
        notify.put_prefs({"move": "false"}, ref=home_league())
    assert notify.prefs(ref=home_league()) == notify.DEFAULT_PREFS


def test_two_moves_by_one_team_collapse_into_one_fragment():
    unsent = [_n("move:1", body="Bears +Isiah Pacheco"), _n("move:2", body="Lions +Z"), _n("move:3", body="Bears −Jaylen Wright")]
    (push,), _ = notify.plan(unsent, notify.DEFAULT_PREFS, T)
    assert push["title"] == "3 league moves" and push["body"] == "Bears +Isiah Pacheco −Jaylen Wright; Lions +Z"
    assert push["keys"] == ["move:1", "move:2", "move:3"]


def test_a_claim_and_its_drop_count_as_one_move():
    unsent = [_n("move:1760000000000-3-WAIVER ADDED-101", body="Bears +Isiah Pacheco"),
              _n("move:1760000000000-3-DROPPED-102", body="Bears −Jaylen Wright"),
              _n("move:1760000000000-5-FA ADDED-103", body="Lions +Z"),
              _n("move:1760000999000-3-DROPPED--16020", body="Bears −Eagles D/ST")]
    (push,), _ = notify.plan(unsent, notify.DEFAULT_PREFS, T)
    assert push["title"] == "3 league moves" and len(push["keys"]) == 4
    (push,), _ = notify.plan(unsent[:2], notify.DEFAULT_PREFS, T)
    assert push["title"] == "1 league move" and push["body"] == "Bears +Isiah Pacheco −Jaylen Wright"


def test_more_count_is_rows_left_out_not_teams():
    unsent = [_n(f"move:{i}", body=f"Team{i // 2} {'+' if i % 2 == 0 else '−'}Somebody Longname{i}") for i in range(40)]
    (push,), _ = notify.plan(unsent, notify.DEFAULT_PREFS, T)
    assert len(push["body"]) <= 180
    shown = push["body"].split("… +")[0].count("Somebody")
    assert push["body"].endswith(f"… +{40 - shown} more")



# --- pushes carry their league (ffl-soc.4.8) ---------------------------------------------------

def _other(name="Indianapolis Pro H2H Points PPR League"):
    from ffl import leagues
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, name, 3, None, None), user_id=0)


def test_league_label_is_the_name_cut_at_a_word():
    from ffl import leagues
    assert notify.league_label(_other()) == "Indianapolis Pro H2H…"
    assert notify.league_label(leagues.LeagueRef("espn:1", "espn", "1", 2026, "2026 Fantasy Football", 7, None, None)) \
        == "2026 Fantasy Football"


def test_one_league_pushes_open_and_tag_their_league():
    db.add_notices([_n("move:1")], ref=home_league())
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)
    sent = []
    notify.flush(T, send=lambda sub, payload: sent.append(payload))
    assert (sent[0]["title"], sent[0]["url"], sent[0]["tag"]) == ("1 league move", f"/transactions?league={HOME_KEY}&seen=moves", f"{HOME_KEY}:moves")


def test_each_leagues_pushes_name_it_and_open_it():
    from ffl import leagues
    other = _other()
    db.add_notices([_n("move:1"), _n("move:2", body="Lions −Y")], ref=home_league())
    db.add_notices([_n("move:1"), {**_n("injury:8:9:OUT", "injury", "Starting at RB."), "url": "/roster"}], ref=other)
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)
    sent = []
    out = notify.flush(T, send=lambda sub, payload: sent.append(payload))
    by_url = {p["url"]: p for p in sent}
    home = by_url[f"/transactions?league={HOME_KEY}&seen=moves"]
    assert home["title"] == f"2 league moves · {home_league().name}", "with two leagues, home's pushes name it too"
    assert home["tag"] == f"{HOME_KEY}:moves", "every league's tap target and tag name it"
    theirs = by_url["/transactions?league=espn:999&seen=moves"]
    assert theirs["title"] == "1 league move · Indianapolis Pro H2H…" and theirs["tag"] == "espn:999:moves"
    assert theirs["notification"]["navigate"].endswith("/transactions?league=espn:999&seen=moves")
    assert by_url["/roster?league=espn:999&seen=injury%3A8%3A9%3AOUT"]["title"] == "t · Indianapolis Pro H2H…"
    assert out["sent"] == 3 and db.unsent_notices(ref=home_league()) == [] and db.unsent_notices(ref=other) == []
    assert {n["outcome"] for n in db.recent_notices(ref=other)} == {"sent"}


def test_a_url_with_a_query_takes_the_league_after_it():
    assert notify.league_url("/players?watch=1", _other()) == "/players?watch=1&league=espn:999"
    assert notify.league_url("/players?watch=1", home_league()) == f"/players?watch=1&league={HOME_KEY}"


def test_switches_are_per_league_quiet_hours_global():
    other = _other()
    assert notify.prefs(ref=other) == notify.DEFAULT_PREFS
    notify.put_prefs({"move": False, "quiet": ["22:00", "06:00"]}, ref=other)
    assert notify.prefs(ref=other)["move"] is False and notify.prefs(ref=home_league())["move"] is True
    assert notify.prefs(ref=home_league())["quiet"] == ["22:00", "06:00"], "quiet hours are every league's"
    notify.put_prefs({"injury": False}, ref=home_league())
    assert notify.prefs(ref=other)["injury"] is True and notify.prefs(ref=home_league())["injury"] is False
    db.add_notices([_n("move:1")], ref=other)
    db.add_notices([_n("move:1")], ref=home_league())
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)
    sent = []
    out = notify.flush(T, send=lambda sub, payload: sent.append(payload))
    assert [p["url"] for p in sent] == [f"/transactions?league={HOME_KEY}&seen=moves"] and out["off"] == 1
    assert db.recent_notices(ref=other)[0]["outcome"] == "off"


def test_a_news_item_queued_in_two_leagues_pushes_once_across_runs():
    other = _other()
    news = {**_n("news:7:1", "news", "Out for the season."), "url": "/news"}
    db.add_notices([news], ref=home_league())
    db.add_notices([news], ref=other)
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=0)
    sent = []
    out = notify.flush(T, send=lambda sub, payload: sent.append(payload))
    assert len(sent) == 1 and out["duplicate"] == 1
    assert sent[0]["title"] == "t", "a news push names no league"
    watch = {**_n("watch:5:2026-10-09", "watch"), "url": "/players"}
    db.add_notices([watch], ref=home_league())
    notify.flush(T, send=lambda sub, payload: sent.append(payload))
    db.add_notices([watch], ref=other)  # a later run, the other league: read back from the table, not memory
    out = notify.flush(T, send=lambda sub, payload: sent.append(payload))
    assert len(sent) == 2 and out["duplicate"] == 1, "a key sent in one league is never sent again in another"
    assert db.recent_notices(ref=other)[0]["outcome"] == "duplicate"
