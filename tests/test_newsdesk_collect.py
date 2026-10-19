import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from ffl import db
from ffl.newsdesk import collect, health
from ffl.newsdesk.players import Index

FIX = Path(__file__).parent / "fixtures" / "newsdesk"
NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
RAMS = {"id": "site:LAR", "kind": "site", "club": "LAR", "name": "therams.com", "url": "https://www.therams.com/rss/news"}
PFT = {"id": "pft", "kind": "pft", "club": None, "name": "Pro Football Talk", "url": "https://profootballtalk.nbcsports.com/feed/"}
RAP = {"id": "bsky:rapsheet.bsky.social", "kind": "bluesky", "club": None, "name": "Ian Rapoport",
       "url": "https://bsky.app/profile/rapsheet.bsky.social"}
ESPN = {"id": "espn:LAR", "kind": "espn", "club": "LAR", "name": "ESPN LAR",
        "url": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?team=14"}
ROTO = {"id": "rotowire", "kind": "rotowire", "club": None, "name": "Rotowire", "url": "https://x"}


class Resp:
    def __init__(self, status=200, body=b"", doc=None):
        self.status_code, self.content, self._doc = status, body, doc

    def json(self):
        return self._doc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise collect.requests.HTTPError(f"{self.status_code}")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    collect._SEEN.clear()
    collect._ROTO.clear()
    monkeypatch.setattr(collect, "_get", lambda url, **kw: pytest.fail(f"unexpected fetch {url}"))


def text(name):
    return (FIX / name).read_bytes()


def items_of(source_id):
    t = db.news_items_table
    with db._read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t).where(t.c.source_id == source_id).order_by(t.c.id))]


def test_parse_rss_team_site():
    got = collect.parse_rss(text("site_rams.xml"), RAMS)
    assert len(got) == 3
    first = got[0]
    assert first["title"] == "From the Podium: Sean McVay, Matthew Stafford and Puka Nacua share thoughts on Rams' loss to Cardinals"
    assert first["url"].startswith("https://www.therams.com/news/from-the-podium-head-coach-sean-mcvay")
    assert first["ext_id"] == "71f9b9dc-dfe1-4cf5-ba20-ba951663d0fc"
    assert first["published_at"] == "2026-10-19T00:32:57+00:00"
    assert first["club"] == "LAR" and first["source_id"] == "site:LAR" and first["at_s"] is None
    assert first["text"].startswith("Rams head coach Sean McVay")


def test_parse_rss_pft_strips_html_and_scripts():
    got = collect.parse_rss(text("pft.xml"), PFT)
    assert [i["title"] for i in got] == ["Sean Payton says he hasn't considered benching Bo Nix",
                                         "For Andy Dalton, the Falcons made the most sense"]
    assert got[0]["published_at"] == "2026-10-19T17:28:59+00:00"  # -0400 in the feed
    assert got[0]["club"] is None
    assert "Nix" in got[0]["text"] and "<" not in got[0]["text"] and "adManagerUrl" not in got[0]["text"]


def test_parse_rss_falls_back_to_link():
    xml = text("site_rams.xml").replace(b'<guid isPermaLink="false">71f9b9dc-dfe1-4cf5-ba20-ba951663d0fc</guid>', b"")
    first = collect.parse_rss(xml, RAMS)[0]
    assert first["ext_id"] == first["url"]


def test_parse_bluesky_skips_reposts():
    got = collect.parse_bluesky(json.loads(text("bsky_feed.json")), RAP)
    assert len(got) == 2
    assert [i["ext_id"] for i in got] == ["at://did:plc:hlvr2omhnmvdmfnvmcuhyevz/app.bsky.feed.post/3myabt5hxxc24",
                                         "at://did:plc:hlvr2omhnmvdmfnvmcuhyevz/app.bsky.feed.post/3my6rl4l63k2a"]
    assert got[0]["url"] == "https://bsky.app/profile/rapsheet.bsky.social/post/3myabt5hxxc24"
    assert got[0]["text"] == "The Colts have fired GM Chris Ballard."
    assert got[0]["published_at"] == "2026-10-19T14:01:10.358000+00:00"


def test_parse_espn_news_keeps_the_clubs_stories():
    got = collect.parse_espn_news(json.loads(text("espn_news.json")), ESPN)
    # the betting piece names every club and the video clip has no text: only Barnwell's Rams story
    assert [i["ext_id"] for i in got] == ["50211303"]
    assert got[0]["url"].startswith("https://www.espn.com/nfl/story/_/id/50211303/")
    assert got[0]["club"] == "LAR" and got[0]["published_at"] == "2026-10-19T14:59:06+00:00"


def test_parse_rotowire_blurbs_only_with_club():
    idx = Index(by_id={3139477: {"name": "Patrick Mahomes", "pos": "QB", "team": "KC"}})
    got = collect.parse_rotowire(json.loads(text("rotowire.json")), ROTO, idx)
    assert [i["ext_id"] for i in got] == ["64319628", "64077928"]
    assert got[0]["club"] == "KC" and got[0]["url"] == "https://www.espn.com/nfl/player/_/id/3139477"
    assert got[0]["title"].startswith("Mahomes completed 23 of 30") and got[0]["text"].startswith("The Chiefs offense")


def test_rotowire_ids_rostered_plus_top_free_agents(monkeypatch):
    monkeypatch.setattr(collect, "ROTOWIRE_FA", 2)
    db.put_page("waivers", {
        "positions": {"QB": [{"player_id": 1, "pos": "QB", "add": 2.0}, {"player_id": 2, "pos": "QB", "add": None}],
                      "RB": [{"player_id": 3, "pos": "RB", "add": 5.0}, {"player_id": 4, "pos": "RB", "add": None}],
                      "D/ST": [{"player_id": -16001, "pos": "D/ST", "add": 9.0}]},
        "rostered": {"WR": [{"player_id": 7, "pos": "WR"}, {"player_id": 8, "pos": "WR"}]}}, 0.0)
    assert collect._rotowire_ids(Index()) == [1, 3, 7, 8]  # the two best adds; no D/ST
    monkeypatch.setattr(collect, "ROTOWIRE_FA", 3)
    # no add: the more-owned of the rest comes next
    assert collect._rotowire_ids(Index(owned={2: 10.0, 4: 60.0})) == [1, 3, 4, 7, 8]


def test_article_text_extracts_paragraphs():
    body = collect.article_text(text("article.html"))
    paras = body.split("\n\n")
    assert len(paras) == 3
    assert paras[0] == "INGLEWOOD, Calif. – The Rams (2-4) fell the Cardinals 18-16 on Sunday at SoFi Stadium."
    assert "Colby Parkinson" in paras[1]
    assert "Tickets" not in body and "var x" not in body and "Los Angeles Rams" not in paras[-1][-30:]


def _register(*sources):
    db.put_news_sources(list(sources))


def _pft_items(**changes):
    got = collect.parse_rss(text("pft.xml"), PFT)
    got[0].update(changes)
    return got


def test_second_poll_inserts_nothing(monkeypatch):
    _register(PFT)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: _pft_items())
    assert collect.run_all(Index(), now=NOW)["new"] == 2
    assert collect.run_all(Index(), now=NOW)["new"] == 0
    rows = items_of("pft")
    assert len(rows) == 2 and all(r["hash"] == collect.item_hash(r["title"], r["text"]) for r in rows)
    assert db.news_sources()[0]["ok_at"] == NOW.isoformat(timespec="seconds")


def test_old_items_are_not_collected(monkeypatch):
    _register(PFT)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: _pft_items())
    assert collect.run_all(Index(), now=NOW + timedelta(days=4))["new"] == 0


def test_failing_source_marked_others_continue(monkeypatch):
    _register(PFT, RAP)

    def poll(source, index=None):
        if source["id"] == "pft":
            raise collect.requests.ConnectionError("down")
        return collect.parse_bluesky(json.loads(text("bsky_feed.json")), source)
    monkeypatch.setattr(collect, "poll", poll)
    out = collect.run_all(Index(), now=NOW)
    assert out["failed"] == ["pft"] and out["new"] == 2
    src = {s["id"]: s for s in db.news_sources()}
    assert src["pft"]["failing_since"] == NOW.isoformat(timespec="seconds") and "down" in src["pft"]["error"]
    assert src[RAP["id"]]["failing_since"] is None and len(items_of(RAP["id"])) == 2


def test_site_items_store_the_article_body(monkeypatch):
    _register(RAMS)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: collect.parse_rss(text("site_rams.xml"), s))
    pages = []

    def get(url, **kw):
        pages.append(url)
        return Resp(404) if "inactives" in url else Resp(body=text("article.html"))
    monkeypatch.setattr(collect, "_get", get)
    assert collect.run_all(Index(), now=NOW)["new"] == 3
    rows = items_of("site:LAR")
    assert len(pages) == 3
    assert rows[0]["text"].startswith("INGLEWOOD, Calif.")
    assert rows[2]["text"] == collect.parse_rss(text("site_rams.xml"), RAMS)[2]["text"]  # page failed: the description
    pages.clear()
    assert collect.run_all(Index(), now=NOW)["new"] == 0 and pages == []  # a known item is not fetched again


def test_espn_story_body_from_the_content_api(monkeypatch):
    _register(ESPN)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: collect.parse_espn_news(json.loads(text("espn_news.json")), s))
    asked = []

    def get(url, **kw):
        asked.append(url)
        return Resp(doc=json.loads(text("espn_story.json")))
    monkeypatch.setattr(collect, "_get", get)
    assert collect.run_all(Index(), now=NOW)["new"] == 1
    assert asked == ["https://content.core.api.espn.com/v1/sports/news/50211303"]
    [row] = items_of("espn:LAR")
    assert row["text"].startswith("It was one thing when the Rams could explain away their losses")


def test_site_title_change_refetches_the_body(monkeypatch):
    _register(RAMS)
    feed = collect.parse_rss(text("site_rams.xml"), RAMS)[:1]
    monkeypatch.setattr(collect, "poll", lambda s, index=None: feed)
    monkeypatch.setattr(collect, "_get", lambda url, **kw: Resp(body=text("article.html")))
    collect.run_all(Index(), now=NOW)
    feed = [{**feed[0], "title": "From the Podium: McVay on the loss"}]
    monkeypatch.setattr(collect, "_get", lambda url, **kw: Resp(503))  # the page fails: the stored body stays
    out = collect.run_all(Index(), now=NOW)
    [row] = items_of("site:LAR")
    assert out["edited"] == 1 and row["title"] == "From the Podium: McVay on the loss"
    assert row["text"].startswith("INGLEWOOD, Calif.")


def test_edited_item_updates_and_retags(monkeypatch):
    _register(PFT)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: _pft_items())
    collect.run_all(Index(), now=NOW)
    first = items_of("pft")[0]
    db.tag_item(first["id"], [1], None, None, NOW.isoformat())
    monkeypatch.setattr(collect, "poll", lambda s, index=None: _pft_items(text="Payton now says Nix is benched."))
    out = collect.run_all(Index(), now=NOW + timedelta(hours=1))
    assert out["new"] == 0 and out["edited"] == 1
    row = items_of("pft")[0]
    assert row["id"] == first["id"] and row["text"] == "Payton now says Nix is benched." and row["tagged_at"] is None
    assert row["hash"] == collect.item_hash(row["title"], row["text"])
    with db._read() as c:
        versions = [dict(r._mapping) for r in c.execute(sa.select(db.news_item_versions))]
    assert len(versions) == 1 and versions[0]["text"] == first["text"]


def test_deleted_bluesky_post_withdrawn(monkeypatch):
    _register(RAP)
    feed = json.loads(text("bsky_feed.json"))
    monkeypatch.setattr(collect, "poll", lambda s, index=None: collect.parse_bluesky(feed, s))
    collect.run_all(Index(), now=NOW)
    kept, deleted = items_of(RAP["id"])
    sid = db.add_story({"first_item_id": deleted["id"], "first_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
                        "kind": "injury", "headline": "h", "overview": ""})
    db.tag_item(deleted["id"], [1], None, sid, NOW.isoformat())
    monkeypatch.setattr(collect, "poll", lambda s, index=None: [])  # both fell off the author feed
    collect.run_all(Index(), now=NOW)
    asked = []

    def get(url, params=None, **kw):
        asked.append(params["uris"])
        return Resp(doc={"posts": [p for p in (e["post"] for e in feed["feed"]) if p["uri"] == kept["ext_id"]]})
    monkeypatch.setattr(collect, "_get", get)
    out = collect.recheck(NOW + timedelta(hours=1))
    assert asked == [[kept["ext_id"], deleted["ext_id"]]] or asked == [[deleted["ext_id"], kept["ext_id"]]]
    assert (out["withdrawn"], out["edited"], out["retracted"]) == (1, 0, [sid])
    rows = {r["id"]: r for r in items_of(RAP["id"])}
    assert rows[deleted["id"]]["withdrawn_at"] is not None and rows[kept["id"]]["withdrawn_at"] is None


def test_article_410_withdrawn(monkeypatch):
    _register(RAMS)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: collect.parse_rss(text("site_rams.xml"), s)[:2])
    monkeypatch.setattr(collect, "_get", lambda url, **kw: Resp(body=text("article.html")))
    collect.run_all(Index(), now=NOW)
    monkeypatch.setattr(collect, "poll", lambda s, index=None: collect.parse_rss(text("site_rams.xml"), s)[:1])
    collect.run_all(Index(), now=NOW)  # the recap fell off the feed
    fetched = []

    def get(url, **kw):
        fetched.append(url)
        return Resp(410)
    monkeypatch.setattr(collect, "_get", get)
    out = collect.recheck(NOW + timedelta(hours=1))
    assert fetched == ["https://www.therams.com/news/game-recap-rams-fall-to-cardinals-18-16-at-sofi-stadium"]
    assert out["withdrawn"] == 1
    podium, recap = items_of("site:LAR")
    assert podium["withdrawn_at"] is None and recap["withdrawn_at"] is not None


def test_recheck_window_48h(monkeypatch):
    _register(RAP)
    old = (NOW - timedelta(days=3)).isoformat()
    db.add_news_items([{"source_id": RAP["id"], "ext_id": "at://old/app.bsky.feed.post/1", "url": "u",
                        "published_at": old, "fetched_at": old, "title": "t", "text": "x", "club": None,
                        "hash": collect.item_hash("t", "x")}])
    monkeypatch.setattr(collect, "poll", lambda s, index=None: [])
    collect.run_all(Index(), now=NOW)
    out = collect.recheck(NOW)  # _get fails the test if anything is fetched
    assert (out["edited"], out["withdrawn"], out["retracted"]) == (0, 0, [])
    assert items_of(RAP["id"])[0]["withdrawn_at"] is None


def test_health_notice_after_24h():
    rows = [{"id": "pft", "name": "Pro Football Talk", "failing_since": (NOW - timedelta(hours=25)).isoformat(),
             "error": "HTTP 503"},
            {"id": "site:LAR", "name": "therams.com", "failing_since": (NOW - timedelta(hours=23)).isoformat(),
             "error": "x"},
            {"id": "espn:LAR", "name": "ESPN LAR", "failing_since": None, "error": None}]
    [n] = health.notices(rows, NOW, "2026-10-19T18:00:00+00:00")
    since = rows[0]["failing_since"]
    assert n["key"] == f"news-source:pft:{since}" and n["kind"] == "news-source"
    assert n["title"] == "Pro Football Talk news failing" and n["url"] == "/news" and "HTTP 503" in n["body"]
    assert n["created_at"] == "2026-10-19T18:00:00+00:00"
