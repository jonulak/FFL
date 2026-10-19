from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from youtube_transcript_api import RequestBlocked, VideoUnavailable

from ffl import db
from ffl.newsdesk import collect, media
from ffl.newsdesk.players import Index

FIX = Path(__file__).parent / "fixtures" / "newsdesk"
NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
YT = {"id": "yt:SF", "kind": "youtube", "club": "SF", "name": "49ers YouTube",
      "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCeIOarQkwmGhimim9cDUTng"}
POD = {"id": "pod:LAR", "kind": "podcast", "club": "LAR", "name": "Between the Horns", "url": "https://omny.test/podcast.rss"}
PRESSER_ID = "Z543WsyPcr0"
INDEX = Index(by_id={1: {"name": "Christian Kirk", "pos": "WR", "team": "SF"},
                     2: {"name": "Brock Purdy", "pos": "QB", "team": "SF"},
                     3: {"name": "Matthew Stafford", "pos": "QB", "team": "LAR"}},
              by_club={"SF": [1, 2], "LAR": [3]},
              by_full={"christian kirk": [1], "brock purdy": [2], "matthew stafford": [3]})
SEGS = [{"start": 0.0, "text": "Injuries for the game."}, {"start": 4.0, "text": "Christian Kirk is questionable."},
        {"start": 200.0, "text": "Brock Purdy looked sharp in practice."}]


class Resp:
    def __init__(self, body=b"", status=200):
        self.status_code, self.content = status, body
        self.text = body.decode()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise collect.requests.HTTPError(f"{self.status_code}")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(media, "_get", lambda url, **kw: pytest.fail(f"unexpected fetch {url}"))
    monkeypatch.setattr(media, "captions", lambda vid: pytest.fail(f"unexpected captions {vid}"))


def fix(name):
    return (FIX / name).read_bytes()


def channel():
    """The 49ers feed with its Shanahan video retitled the way the Bears title theirs, so it reads as a presser."""
    return fix("yt_channel.xml").replace(b"Shanahan Previews Commanders Matchup, Injury Updates | 49ers",
                                          b"Kyle Shanahan Postgame Press Conference")


def serve(monkeypatch, pages: dict):
    monkeypatch.setattr(media, "_get", lambda url, **kw: Resp(pages[url]) if url in pages else pytest.fail(url))


def items_of(source_id):
    t = db.news_items_table
    with db._read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t).where(t.c.source_id == source_id).order_by(t.c.id))]


def test_is_presser():
    assert media.is_presser("Kyle Shanahan Postgame Press Conference")
    assert media.is_presser("Ben Johnson podium")
    assert not media.is_presser("Top 10 plays")
    assert not media.is_presser("Hype video")


def test_parse_channel_feed():
    got = media.parse_channel_feed(fix("yt_channel.xml").decode(), YT)
    assert len(got) == 3
    assert got[2] == {"video_id": PRESSER_ID, "title": "Shanahan Previews Commanders Matchup, Injury Updates | 49ers",
                      "published_at": "2026-10-17T21:55:06+00:00"}


def test_parse_podcast_feed_prefers_srt():
    got = media.parse_podcast_feed(fix("omny.xml").decode(), POD)
    assert len(got) == 2
    ep = got[0]
    assert ep["guid"] == "ec1c4921-2928-401c-a8e3-b4e401756f85"
    assert ep["title"].startswith("Ep. 251: Takeaways from the Rams-Bills battle on MNF") and "&amp;" not in ep["title"]
    assert ep["published_at"] == "2026-10-15T22:59:00+00:00"
    assert ep["url"] == "https://omny.fm/shows/between-the-horns/audio-261015-bth-wk-06-v04-m4a"
    assert ep["transcript_type"] == "application/srt"
    assert "format=SubRip&t=1792104198" in ep["transcript_url"]


def test_parse_vtt_and_srt():
    for name, type_ in (("transcript.vtt", "text/vtt"), ("transcript.srt", "application/srt")):
        got = media.parse_transcript(fix(name).decode(), type_)
        assert len(got) == 5, name
        assert got[1] == {"start": 10.81, "text": "Toyota dealers. We're coming to you from the Toyota Patio"}, name


def test_parse_plain_transcript_is_one_seg():
    assert media.parse_transcript("Hello there.\nGeneral Kenobi.", "text/plain") == [
        {"start": 0.0, "text": "Hello there. General Kenobi."}]


def test_items_for_ext_id_and_timestamp_url():
    vid = {"id": PRESSER_ID, "source_id": "yt:SF", "url": f"https://www.youtube.com/watch?v={PRESSER_ID}",
           "title": "Kyle Shanahan Postgame Press Conference", "published_at": "2026-10-17T21:55:06+00:00"}
    got = media.items_for(vid, SEGS, INDEX, ("SF",))
    assert [i["ext_id"] for i in got] == [f"{PRESSER_ID}#1", f"{PRESSER_ID}#2"]
    kirk, purdy = got
    assert kirk["url"] == f"https://www.youtube.com/watch?v={PRESSER_ID}&t=0s" and kirk["at_s"] == 0.0
    assert purdy["url"] == f"https://www.youtube.com/watch?v={PRESSER_ID}&t=170s" and purdy["at_s"] == 170.0
    assert kirk["title"] == vid["title"] and kirk["club"] == "SF" and kirk["source_id"] == "yt:SF"
    assert "Christian Kirk is questionable." in kirk["text"] and "Purdy" not in kirk["text"]

    ep = {"id": "guid-1", "source_id": "pod:SF", "url": "https://omny.fm/shows/x/ep", "title": "Ep. 9",
          "published_at": "2026-10-17T21:55:06+00:00"}
    got = media.items_for(ep, SEGS, INDEX, ("SF",))
    assert got[1]["ext_id"] == "guid-1#2" and got[1]["url"] == "https://omny.fm/shows/x/ep" and got[1]["at_s"] == 170.0


def test_missing_captions_kept_pending_then_lag_recorded(monkeypatch):
    db.put_news_sources([YT])
    serve(monkeypatch, {YT["url"]: channel()})
    answers = [None, SEGS]
    monkeypatch.setattr(media, "captions", lambda vid: answers.pop(0) if vid == PRESSER_ID else pytest.fail(vid))

    first = media.run_all(INDEX, now=NOW)
    assert first["new"] == 0 and first["pending"] == 1
    assert db.get_state("news_pending_media") == {PRESSER_ID: NOW.isoformat(timespec="seconds")}
    assert db.news_media(PRESSER_ID) is None and items_of("yt:SF") == []

    later = NOW + timedelta(hours=2)
    second = media.run_all(INDEX, now=later)
    assert second["new"] == 2 and second["pending"] == 0
    assert db.get_state("news_pending_media") == {}
    published = datetime(2026, 10, 17, 21, 55, 6, tzinfo=timezone.utc)
    assert db.get_state("news_caption_lag") == [round((later - published).total_seconds() / 60, 1)]
    stored = db.news_media(PRESSER_ID)
    assert stored["segs"] == SEGS and stored["source_id"] == "yt:SF"
    assert [i["ext_id"] for i in items_of("yt:SF")] == [f"{PRESSER_ID}#1", f"{PRESSER_ID}#2"]
    assert db.news_sources()[0]["ok_at"] is not None


def test_pending_stops_retrying_after_a_day_and_is_kept(monkeypatch):
    db.put_news_sources([YT])
    serve(monkeypatch, {YT["url"]: channel()})
    seen = (NOW - timedelta(hours=25)).isoformat(timespec="seconds")
    db.put_state("news_pending_media", {PRESSER_ID: seen})
    got = media.run_all(INDEX, now=NOW)  # captions would fail the test
    assert got["new"] == 0
    assert db.get_state("news_pending_media") == {PRESSER_ID: seen}


def test_second_run_inserts_nothing(monkeypatch):
    db.put_news_sources([YT])
    serve(monkeypatch, {YT["url"]: channel()})
    monkeypatch.setattr(media, "captions", lambda vid: SEGS)
    assert media.run_all(INDEX, now=NOW)["new"] == 2
    monkeypatch.setattr(media, "captions", lambda vid: pytest.fail("refetched captions"))
    assert media.run_all(INDEX, now=NOW + timedelta(minutes=15))["new"] == 0
    assert len(items_of("yt:SF")) == 2
    assert db.get_state("news_caption_lag") and len(db.get_state("news_caption_lag")) == 1


def test_captions_capped_per_run(monkeypatch):
    db.put_news_sources([YT])
    entry = channel().split(b" <entry>")[3].split(b"</feed>")[0]
    feed = channel().split(b" <entry>")[0] + b"".join(
        b" <entry>" + entry.replace(PRESSER_ID.encode(), f"vid{n:08d}xx".encode()) for n in range(media.CAPTIONS_PER_RUN + 3)
    ) + b"</feed>\n"
    serve(monkeypatch, {YT["url"]: feed})
    calls = []
    monkeypatch.setattr(media, "captions", lambda vid: calls.append(vid))
    got = media.run_all(INDEX, now=NOW)
    assert len(calls) == media.CAPTIONS_PER_RUN and got["pending"] == media.CAPTIONS_PER_RUN


def test_blocked_captions_fail_the_source_and_stop(monkeypatch):
    db.put_news_sources([YT])
    serve(monkeypatch, {YT["url"]: channel()})

    def blocked(vid):
        raise RequestBlocked(vid)
    monkeypatch.setattr(media, "captions", blocked)
    got = media.run_all(INDEX, now=NOW)
    assert got["failed"] == ["yt:SF"]
    assert db.get_state("news_pending_media", {}) == {}
    assert db.news_sources()[0]["error"].startswith("RequestBlocked")


def test_one_unavailable_video_does_not_stop_the_rest(monkeypatch):
    db.put_news_sources([YT])
    entry = channel().split(b" <entry>")[3].split(b"</feed>")[0]
    feed = channel().split(b" <entry>")[0] + b"".join(
        b" <entry>" + entry.replace(PRESSER_ID.encode(), vid) for vid in (b"gone0000000", b"good0000000")) + b"</feed>\n"
    serve(monkeypatch, {YT["url"]: feed})

    def caps(vid):
        if vid == "gone0000000":
            raise VideoUnavailable(vid)
        return SEGS
    monkeypatch.setattr(media, "captions", caps)
    got = media.run_all(INDEX, now=NOW)
    assert got["new"] == 2 and got["failed"] == ["yt:SF"]
    assert db.news_media("good0000000") is not None


def test_podcast_episode_stored_and_split(monkeypatch):
    db.put_news_sources([POD])
    eps = media.parse_podcast_feed(fix("omny.xml").decode(), POD)
    srt = (b"1\n00:00:07,930 --> 00:00:10,760\nSpeaker 1: Matthew Stafford was sharp.\n\n"
           b"2\n00:00:20,000 --> 00:00:22,000\nSpeaker 2: Agreed.\n")
    serve(monkeypatch, {POD["url"]: fix("omny.xml"), eps[0]["transcript_url"]: srt})
    got = media.run_all(INDEX, now=datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    assert got["new"] == 1
    item = items_of("pod:LAR")[0]
    assert item["ext_id"] == f"{eps[0]['guid']}#3" and item["url"] == eps[0]["url"] and item["at_s"] == 0.0
    assert item["text"] == "Matthew Stafford was sharp. Agreed."
    assert db.news_media(eps[0]["guid"])["segs"][0] == {"start": 7.93, "text": "Matthew Stafford was sharp."}
    assert db.get_state("news_caption_lag", []) == []  # a podcast's transcript is no caption


def test_collect_skips_media_kinds(monkeypatch):
    db.put_news_sources([YT, POD])
    monkeypatch.setattr(collect, "_get", lambda url, **kw: pytest.fail(f"collect fetched {url}"))
    assert collect.run_all(INDEX, now=NOW) == {"new": 0, "edited": 0, "failed": []}
