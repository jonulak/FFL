import sqlalchemy as sa

from ffl import db
from tests.home import home_league


def item(ext, source="site:LAR", at="2026-10-18T10:00:00", **kw):
    return {"source_id": source, "ext_id": ext, "url": f"https://x/{ext}", "published_at": at, "fetched_at": at,
            "title": f"t{ext}", "text": f"body {ext}", "club": "LAR", "hash": f"h{ext}", **kw}


def story(at, headline="h", players=(), **kw):
    sid = db.add_story({"first_item_id": None, "first_at": at, "updated_at": at, "kind": "injury",
                        "headline": headline, "overview": "", **kw})
    db.put_story_players(sid, [{"player_id": p, "effect": "down", "kind": "injury", "prob": 0.5, "confidence": 0.5}
                               for p in players])
    return sid


def test_put_news_media_roundtrip():
    segs = [{"start": 0.0, "text": "a"}, {"start": 12.5, "text": "b"}]
    db.put_news_media({"id": "vid1", "source_id": "yt:LAR", "url": "u", "title": "T", "published_at": "2026-10-18",
                       "segs": segs, "fetched_at": "2026-10-18T10:00:00"})
    assert db.news_media("vid1")["segs"] == segs
    db.put_news_media({"id": "vid1", "source_id": "yt:LAR", "url": "u", "title": "T2", "published_at": "2026-10-18",
                       "segs": segs[:1], "fetched_at": "2026-10-18T11:00:00"})
    assert db.news_media("vid1")["title"] == "T2" and len(db.news_media("vid1")["segs"]) == 1
    assert db.news_media("nope") is None


def test_update_item_text_keeps_version():
    [i] = db.add_news_items([item("a")])
    db.tag_item(i, [1], None, None, "2026-10-18T11:00:00")
    assert db.untagged_items() == []
    db.update_item_text(i, "new title", "new body", "h2", "2026-10-18T12:00:00")
    with db._read() as c:
        vs = [dict(r._mapping) for r in c.execute(sa.select(db.news_item_versions))]
    assert len(vs) == 1 and (vs[0]["title"], vs[0]["text"], vs[0]["hash"]) == ("ta", "body a", "ha")
    assert vs[0]["seen_at"] == "2026-10-18T12:00:00"
    [u] = db.untagged_items()
    assert (u["id"], u["title"], u["text"], u["hash"], u["tagged_at"]) == (i, "new title", "new body", "h2", None)


def test_withdraw_last_item_retracts_story():
    a, b = db.add_news_items([item("a"), item("b")])
    sid = story("2026-10-18T10:00:00", players=[1])
    db.tag_item(a, [1], None, sid, "t")
    db.tag_item(b, [1], None, sid, "t")
    assert db.withdraw_item(a, "2026-10-18T12:00:00") is None
    assert db.story(sid)["status"] == "live" and db.story(sid)["rev"] == 1
    s = db.withdraw_item(b, "2026-10-18T13:00:00")
    assert s["status"] == "retracted"
    got = db.story(sid)
    assert (got["status"], got["rev"], got["status_at"]) == ("retracted", 2, "2026-10-18T13:00:00")
    assert got["updated_at"] == "2026-10-18T13:00:00"  # re-surfaces, like set_story_status
    assert db.withdraw_item(db.add_news_items([item("c")])[0], "t") is None  # no story at all


def test_retracted_story_stays_in_feed():
    [a] = db.add_news_items([item("a")])
    sid = story("2026-10-18T10:00:00", players=[1])
    db.tag_item(a, [1], None, sid, "t")
    assert db.put_story_words(sid, 1, "H", "O", {1: ("why", False)})
    db.withdraw_item(a, "2026-10-18T13:00:00")
    db.put_story_words(sid, 2, "H", "O", {1: ("why", False)})
    [s] = db.news_feed(None, 10)
    assert s["status"] == "retracted" and s["players"][0]["player_id"] == 1


def test_add_news_items_dedupes():
    ids = db.add_news_items([item("a"), item("b")])
    assert len(ids) == 2
    assert db.add_news_items([item("a"), item("b")]) == []
    [c] = db.add_news_items([item("a"), item("c")])
    assert db.news_item_hashes("site:LAR", ["a", "c", "zz"]) == {"a": (ids[0], "ha"), "c": (c, "hc")}
    assert db.add_news_items([]) == []


def test_mark_news_source_failing_since_kept():
    db.put_news_sources([{"id": "pft", "kind": "pft", "club": None, "name": "PFT", "url": "u", "active": True}])
    db.mark_news_source("pft", False, "boom", "t1")
    db.mark_news_source("pft", False, "boom2", "t2")
    [s] = db.news_sources()
    assert (s["failing_since"], s["error"], s["ok_at"]) == ("t1", "boom2", None)
    db.mark_news_source("pft", True, None, "t3")
    [s] = db.news_sources()
    assert (s["failing_since"], s["error"], s["ok_at"]) == (None, None, "t3")


def test_news_sources_upsert_and_active_filter():
    row = {"id": "pft", "kind": "pft", "club": None, "name": "PFT", "url": "u", "active": True}
    db.put_news_sources([row, {**row, "id": "rotowire", "kind": "rotowire", "active": False}])
    db.put_news_sources([{**row, "name": "PFT2"}])
    assert [s["name"] for s in db.news_sources()] == ["PFT2"]
    assert len(db.news_sources(active_only=False)) == 2


def test_recent_stories_window_and_players():
    [a] = db.add_news_items([item("a")])
    s1 = story("2026-10-18T10:00:00", players=[1])
    story("2026-10-14T10:00:00", players=[1])
    story("2026-10-18T10:00:00", players=[2])
    db.tag_item(a, [1], None, s1, "t")
    got = db.recent_stories([1], "2026-10-16T00:00:00")
    assert [s["id"] for s in got] == [s1]
    assert got[0]["players"][0]["player_id"] == 1 and got[0]["items"][0]["id"] == a
    assert db.recent_stories([], "2026-10-01") == []


def test_recent_stories_leave_out_retracted():
    [a] = db.add_news_items([item("a")])
    sid = story("2026-10-18T10:00:00", players=[1])
    db.tag_item(a, [1], None, sid, "t")
    assert [s["id"] for s in db.recent_stories([1], "2026-10-16T00:00:00")] == [sid]
    db.withdraw_item(a, "2026-10-18T13:00:00")
    assert db.recent_stories([1], "2026-10-16T00:00:00") == []  # a new item never joins a retracted story


def test_player_stories_groups_by_player_newest_first_without_retracted_or_old():
    old = story("2026-10-01T00:00:00", players=[1])
    a = story("2026-10-15T00:00:00", players=[1, 2])
    b = story("2026-10-17T00:00:00", players=[1])
    gone = story("2026-10-18T00:00:00", players=[1])
    db.set_story_status(gone, "retracted", "2026-10-18T01:00:00", None)
    story("2026-10-18T00:00:00", players=[3])
    got = db.player_stories([1, 2], "2026-10-10T00:00:00")
    assert [s["id"] for s in got[1]] == [b, a] and [s["id"] for s in got[2]] == [a]
    assert 3 not in got and old not in [s["id"] for s in got[1]]
    assert {r["player_id"] for r in got[2][0]["players"]} == {1, 2}
    assert db.player_stories([], "2026-10-10") == {}


def test_put_story_words_rejects_stale_rev():
    sid = story("2026-10-18T10:00:00", headline="old", players=[1, 2])
    db.update_story(sid, {"rev": 2})
    assert db.put_story_words(sid, 1, "new", "ov", {1: ("r", True)}) is False
    assert db.story(sid)["headline"] == "old"
    assert db.put_story_words(sid, 2, "new", "ov", {1: ("r1", True), 2: ("r2", False)}) is True
    s = db.story(sid)
    assert (s["headline"], s["overview"], s["written_rev"]) == ("new", "ov", 2)
    with db._read() as c:
        rows = {r.player_id: (r.reason, r.flag) for r in c.execute(sa.select(db.news_story_players))}
    assert rows == {1: ("r1", True), 2: ("r2", False)}
    assert db.unwritten_stories(10) == []


def test_unwritten_stories_newest_first():
    a = story("2026-10-18T10:00:00", players=[1])
    b = story("2026-10-17T10:00:00", players=[1])
    assert [s["id"] for s in db.unwritten_stories()] == [a, b]
    assert [s["id"] for s in db.unwritten_stories(1)] == [a]


def test_set_story_status_bumps_rev():
    sid = story("2026-10-18T10:00:00")
    db.set_story_status(sid, "corrected", "2026-10-19T09:00:00", "PFT")
    s = db.story(sid)
    assert (s["status"], s["rev"], s["updated_at"], s["status_note"]) == ("corrected", 2, "2026-10-19T09:00:00", "PFT")


def test_news_feed_hides_unwritten_and_pages():
    s1 = story("2026-10-15T10:00:00", players=[1])
    s2 = story("2026-10-16T10:00:00", players=[2])
    s3 = story("2026-10-17T10:00:00", players=[1])
    story("2026-10-18T10:00:00", players=[1])  # unwritten
    for s in (s1, s2, s3):
        db.put_story_words(s, 1, "H", "O", {})
    assert [s["id"] for s in db.news_feed(None, 2)] == [s3, s2]
    assert [s["id"] for s in db.news_feed("2026-10-16T10:00:00", 2)] == [s1]
    assert [s["id"] for s in db.news_feed(None, 10, player_id=1)] == [s3, s1]
    assert [s["id"] for s in db.news_feed(None, 10, since="2026-10-16T00:00:00")] == [s3, s2]


def test_news_feed_player_only_where_he_is_a_subject():
    moved = story("2026-10-15T10:00:00", players=[1])
    named = story("2026-10-16T10:00:00", players=[2])
    db.put_story_players(named, [{"player_id": 1, "effect": "none", "kind": "role", "prob": 0.5, "confidence": 0.5}])
    for s in (moved, named):
        db.put_story_words(s, 1, "H", "O", {})
    assert [s["id"] for s in db.news_feed(None, 10, player_id=1)] == [moved]
    assert [s["id"] for s in db.news_feed(None, 10, player_id=2)] == [named]


def test_feed_pages_through_equal_timestamps():
    ids = [story("2026-10-17T10:00:00") for _ in range(5)]
    for sid in ids:
        db.put_story_words(sid, 1, "H", "O", {})
    seen, cursor = [], None
    for _ in range(3):
        page = db.news_feed(cursor, 2)
        seen += [s["id"] for s in page]
        cursor = f"{page[-1]['latest_at']}|{page[-1]['id']}"
    assert seen == sorted(ids, reverse=True) and db.news_feed(cursor, 2) == []


def test_feed_orders_by_latest_news():
    """The feed's order is the news's time, not when the app last touched a story: a backlog run processes old news
    late, and an update (its newest item) re-surfaces the story."""
    old = story("2026-10-17T10:00:00", players=[1])          # processed now, its news two days old
    db.update_story(old, {"updated_at": "2026-10-19T12:00:00"})
    fresh = story("2026-10-19T08:00:00", players=[2])
    grown = story("2026-10-16T10:00:00", players=[1])         # first news oldest, but an update came in last
    a, b, c, d = db.add_news_items([item("a", at="2026-10-17T10:00:00"), item("b", at="2026-10-19T08:00:00"),
                                    item("c", at="2026-10-16T10:00:00"), item("d", at="2026-10-19T09:00:00")])
    for i, sid in ((a, old), (b, fresh), (c, grown), (d, grown)):
        db.tag_item(i, [1], None, sid, "2026-10-19T12:00:00")
    for sid in (old, fresh, grown):
        db.put_story_words(sid, 1, "H", "O", {})
    assert db.story(grown)["latest_at"] == "2026-10-19T09:00:00"
    assert [s["id"] for s in db.news_feed(None, 10)] == [grown, fresh, old]
    page = db.news_feed(None, 1)
    assert [s["id"] for s in db.news_feed(f"{page[-1]['latest_at']}|{page[-1]['id']}", 10)] == [fresh, old]
    assert [s["id"] for s in db.news_feed(None, 10, since="2026-10-19T00:00:00")] == [grown, fresh]
    # a withdrawn update hands the time back to the newest source left
    db.withdraw_item(d, "2026-10-19T13:00:00")
    assert db.story(grown)["latest_at"] == "2026-10-16T10:00:00"
    assert [s["id"] for s in db.news_feed(None, 10)] == [fresh, old, grown]


def test_reword_rewrites_at_the_same_rev():
    sid = story("2026-10-18T10:00:00", players=[1])
    assert db.put_story_words(sid, 1, "H", "O", {})
    assert db.unwritten_stories() == []
    db.ask_reword(sid)
    [s] = db.unwritten_stories()
    assert (s["rev"], s["written_rev"], s["reword"]) == (1, 1, 1)
    db.ask_reword(sid)  # asked again while the first was being written: that write is stale
    assert db.put_story_words(sid, 1, "H2", "O", {}, reword=1) is False
    assert db.put_story_words(sid, 1, "H2", "O", {}, reword=2) is True
    s = db.story(sid)
    assert (s["headline"], s["rev"], s["reword"]) == ("H2", 1, 0) and db.unwritten_stories() == []


def test_notice_keys_only_sent():
    rows = [{"key": k, "kind": "news", "title": "t", "body": "b", "url": "u", "created_at": "t"}
            for k in ("news:1", "news:2", "news:3", "move:1")]
    db.add_notices(rows, ref=home_league())
    db.mark_notices(["news:1", "move:1"], "sent", "t", ref=home_league())
    db.mark_notices(["news:2"], "off", "t", ref=home_league())
    assert db.notice_keys("news:", ref=home_league()) == ["news:1"]
