"""The in-app list over the notices table: what shows, unread counts, read and delete, a push tap, the Home Screen badge."""
from datetime import datetime, timezone

import pytest

from ffl import db, notify
from tests import two_users
from tests.two_users import act_as

T = "2026-10-09T10:00:00-04:00"
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _n(key, kind="injury", title="Hurt", body="b", at=T, url="/roster"):
    return {"key": key, "kind": kind, "title": title, "body": body, "url": url, "created_at": at}


@pytest.fixture
def two():
    return two_users.make()


@pytest.fixture
def client():
    return two_users.client()


def _rows(item):
    return item["rows"]


def test_list_is_newest_first_with_the_true_count_beyond_the_limit(two):
    db.add_notices([_n("a", at="2026-10-09T10:00:00-04:00"), _n("b", at="2026-10-09T11:00:00-04:00")], ref=two.a)
    out = notify.inbox(two.a_id, limit=1)
    assert out["unread"] == 2 and [i["rows"][0]["key"] for i in out["items"]] == ["b"]
    assert out["items"][0]["read"] is False and out["items"][0]["url"].endswith(f"league={two.a.key}")


def test_a_claim_and_its_drop_are_one_item_read_together(two):
    db.add_notices([_n("move:1700000000000-3-WAIVER ADDED-1", "move", "League move", "Bears +A", url="/transactions"),
                    _n("move:1700000000000-3-DROPPED-2", "move", "League move", "Bears −B", url="/transactions")], ref=two.a)
    out = notify.inbox(two.a_id)
    assert out["unread"] == 1 and out["items"][0]["body"] == "Bears −B +A"  # as the push reads
    notify.read(_rows(out["items"][0]), user_id=two.a_id)
    assert notify.inbox(two.a_id)["unread"] == 0


def test_read_stays_listed_and_stops_a_queued_push(two):
    db.add_notices([_n("a")], ref=two.a)
    notify.read(_rows(notify.inbox(two.a_id)["items"][0]), user_id=two.a_id)
    out = notify.inbox(two.a_id)
    assert out["unread"] == 0 and out["items"][0]["read"] is True
    assert db.unsent_notices(ref=two.a) == []


def test_read_all_clears_every_league(two):
    db.add_notices([_n("a")], ref=two.b)
    db.add_notices([_n("b")], ref=two.b_only)
    assert notify.inbox(two.b_id)["unread"] == 2
    notify.read_all(user_id=two.b_id)
    assert notify.inbox(two.b_id)["unread"] == 0


def test_a_deleted_notice_does_not_come_back_when_its_event_is_seen_again(two):
    db.add_notices([_n("a")], ref=two.a)
    notify.delete(_rows(notify.inbox(two.a_id)["items"][0]), user_id=two.a_id)
    assert notify.inbox(two.a_id)["items"] == []
    assert db.add_notices([_n("a")], ref=two.a) == 0  # the detector sees it again: the row is what remembers
    assert notify.inbox(two.a_id)["items"] == [] and db.unsent_notices(ref=two.a) == []


def test_the_list_switch_is_separate_from_the_push_switch(two):
    db.add_notices([_n("a")], ref=two.a)
    notify.put_prefs({"injury": False}, ref=two.a)  # push off: still listed
    assert notify.inbox(two.a_id)["unread"] == 1
    notify.put_prefs({"injury": True, "app_injury": False}, ref=two.a)  # list off: hidden, still pushed
    assert notify.inbox(two.a_id) == {"unread": 0, "items": []}
    assert [n["key"] for n in db.unsent_notices(ref=two.a)] == ["a"]
    assert notify.prefs(ref=two.a)["app_injury"] is False and notify.prefs(ref=two.a)["injury"] is True


def test_a_trade_follows_the_move_list_switch(two):
    db.add_notices([_n("move:x", "trade", "League move", "Bears get A", url="/transactions")], ref=two.a)
    assert notify.inbox(two.a_id)["items"][0]["title"] == "Trade"
    notify.put_prefs({"app_move": False}, ref=two.a)
    assert notify.inbox(two.a_id)["items"] == []


def test_a_list_switch_refuses_a_non_boolean(two):
    with pytest.raises(ValueError):
        notify.put_prefs({"app_news": "no"}, ref=two.a)


def test_news_in_two_leagues_is_one_item_and_reading_it_reads_both(two):
    db.add_notices([_n("news:1", "news")], ref=two.b)
    db.add_notices([_n("news:1", "news")], ref=two.b_only)
    out = notify.inbox(two.b_id)
    assert out["unread"] == 1 and len(_rows(out["items"][0])) == 2
    assert out["items"][0]["league"] is None
    notify.read(_rows(out["items"][0])[:1], user_id=two.b_id)  # one league's row is enough
    assert notify.inbox(two.b_id)["unread"] == 0


def test_the_same_key_in_two_leagues_is_two_items_when_it_is_each_leagues_own(two):
    db.add_notices([_n("injury:5:9:OUT")], ref=two.b)
    db.add_notices([_n("injury:5:9:OUT")], ref=two.b_only)
    out = notify.inbox(two.b_id)
    assert out["unread"] == 2
    assert {i["league"] for i in out["items"]} == {notify.league_label(two.b), notify.league_label(two.b_only)}


def test_no_league_label_with_one_league(two):
    db.add_notices([_n("a")], ref=two.a)
    assert notify.inbox(two.a_id)["items"][0]["league"] is None


def test_a_left_league_drops_out_of_the_list(two):
    db.add_notices([_n("a")], ref=two.b)
    db.add_notices([_n("b")], ref=two.b_only)
    assert notify.inbox(two.b_id)["unread"] == 2
    db.drop_memberships(two.b_id, [two.b.key])
    assert [i["rows"][0]["key"] for i in notify.inbox(two.b_id)["items"]] == ["b"]


def test_tapping_a_grouped_push_reads_every_move_it_replaced_and_nothing_else(two):
    db.add_notices([_n("move:1", "move", url="/transactions"), _n("move:2", "move", url="/transactions"),
                    _n("move:3", "move", url="/transactions"), _n("a")], ref=two.a)
    db.mark_notices(["move:1", "move:2", "a"], "sent", T, ref=two.a)
    notify.read_pushed("moves", ref=two.a)
    unread = {i["rows"][0]["key"] for i in notify.inbox(two.a_id)["items"] if not i["read"]}
    assert unread == {"move:3", "a"}  # move:3 has not been pushed: this tap cannot have been about it


def test_tapping_a_single_push_reads_only_its_notice(two):
    db.add_notices([_n("a"), _n("b")], ref=two.a)
    db.mark_notices(["a", "b"], "sent", T, ref=two.a)
    notify.read_pushed("a", ref=two.a)
    assert {i["rows"][0]["key"]: i["read"] for i in notify.inbox(two.a_id)["items"]} == {"a": True, "b": False}


def test_a_flushed_push_names_its_tag_and_carries_the_unread_count(two):
    db.add_notices([_n("injury:1"), _n("injury:2")], ref=two.a)
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=two.a_id)
    sent = []
    notify.flush(NOW, send=lambda sub, payload: sent.append(payload))
    assert len(sent) == 2 and {p["notification"]["app_badge"] for p in sent} == {"2"}
    assert {p["app_badge"] for p in sent} == {2}
    assert f"league={two.a.key}&seen=injury%3A1" in sent[0]["url"] + sent[1]["url"]
    assert "seen=injury%3A" in sent[0]["notification"]["navigate"]


def test_the_test_push_touches_neither_badge_nor_read_state(two):
    db.put_subscription("https://push/a", {"p256dh": "k", "auth": "a"}, user_id=two.a_id)
    sent = []
    notify.send_test(two.a_id, send=lambda sub, payload: sent.append(payload))
    assert "app_badge" not in sent[0] and "app_badge" not in sent[0]["notification"] and "seen=" not in sent[0]["url"]


# --- routes ---

def test_routes_list_read_seen_and_delete(two, client, monkeypatch):
    act_as(monkeypatch, two.a_id)
    db.add_notices([_n("a"), _n("b"), _n("c")], ref=two.a)
    db.mark_notices(["c"], "sent", T, ref=two.a)
    out = client.get("/notifications").json()
    assert out["unread"] == 3 and len(out["items"]) == 3
    by_key = {i["rows"][0]["key"]: i for i in out["items"]}
    assert client.get("/notifications", params={"limit": 0}).json() == {"unread": 3, "items": []}
    assert client.post("/notifications/read", json={"rows": by_key["a"]["rows"]}).json() == {"unread": 2}
    assert client.post("/notifications/seen", json={"tag": "c"}, params={"league": two.a.key}).json() == {"unread": 1}
    assert client.post("/notifications/delete", json={"rows": by_key["b"]["rows"]}).json() == {"unread": 0}
    assert len(client.get("/notifications").json()["items"]) == 2
    db.add_notices([_n("d")], ref=two.a)
    assert client.post("/notifications/read", json={"all": True}).json() == {"unread": 0}


def test_read_needs_rows_or_all(two, client, monkeypatch):
    act_as(monkeypatch, two.a_id)
    assert client.post("/notifications/read", json={}).status_code == 422


def test_one_user_cannot_see_or_change_anothers_notifications(two, client, monkeypatch):
    db.add_notices([_n("mine")], ref=two.a)
    row = {"league": two.a.key, "key": "mine"}
    act_as(monkeypatch, two.b_id)
    assert client.get("/notifications").json() == {"unread": 0, "items": []}
    client.post("/notifications/read", json={"rows": [row]})
    client.post("/notifications/delete", json={"rows": [row]})
    client.post("/notifications/read", json={"all": True})
    assert client.post("/notifications/seen", json={"tag": "mine"}, params={"league": two.a.key}).json() == {"unread": 0}
    out = notify.inbox(two.a_id)
    assert out["unread"] == 1 and out["items"][0]["read"] is False
