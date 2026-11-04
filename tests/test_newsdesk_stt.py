"""Podcast episodes without a transcript: queued by media.run_all for the stt service (ffl/stt.py), transcribed in
the pipeline's last stage (media.transcribe_pending) and stored like a transcript the feed shipped."""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from ffl import db
from ffl.newsdesk import media
from tests.test_newsdesk_media import INDEX, POD, Resp, fix, items_of, serve  # noqa: F401 — the _offline fixture too

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)  # the fixture's newer episode is a day old, the older a week


def _no_transcript():
    return re.sub(rb"\s*<podcast:transcript [^>]*/>", b"", fix("omny.xml"))


def _fresh_ep():
    return max(media.parse_podcast_feed(_no_transcript(), POD), key=lambda e: e["published_at"])


def _post(monkeypatch, reply, posts=None):
    def post(url, json=None, timeout=None, headers=None):  # noqa: A002
        if posts is not None:
            posts.append((url, json, headers))
        if isinstance(reply, Exception):
            raise reply
        return Resp(globals()["json"].dumps(reply).encode())
    monkeypatch.setattr(media.requests, "post", post)


def test_episode_without_transcript_queues_only_with_an_stt(monkeypatch):
    db.put_news_sources([POD])
    ep = _fresh_ep()
    assert ep["transcript_url"] is None and ep["audio_url"].startswith("https://podtrac.com/")
    serve(monkeypatch, {POD["url"]: _no_transcript()})
    assert media.run_all(INDEX, now=NOW)["media"] == 0
    assert db.get_state("news_pending_audio", {}) == {}  # no FFL_STT_URL: the episode is skipped as before
    monkeypatch.setenv("FFL_STT_URL", "http://stt:8320")
    media.run_all(INDEX, now=NOW + timedelta(hours=2))
    queue = db.get_state("news_pending_audio")
    assert list(queue) == [ep["guid"]]  # the older episode is past three days
    assert queue[ep["guid"]] == {"source_id": "pod:LAR", "club": "LAR", "title": ep["title"], "url": ep["url"],
                                 "audio_url": ep["audio_url"], "published_at": ep["published_at"]}
    assert db.get_state("news_podcast_polled")["pod:LAR"]["at"], "queuing is not a failure: the poll is recorded"


def test_transcribe_pending_stores_the_episode_like_a_transcript(monkeypatch):
    db.put_news_sources([POD])
    ep = _fresh_ep()
    monkeypatch.setenv("FFL_STT_URL", "http://stt:8320/")
    monkeypatch.setenv("FFL_JOB_TOKEN", "job-secret")
    serve(monkeypatch, {POD["url"]: _no_transcript()})
    media.run_all(INDEX, now=NOW)
    segs = [{"start": 7.93, "text": "Matthew Stafford was sharp."}, {"start": 20.0, "text": "Agreed."}]
    posts = []
    _post(monkeypatch, {"ok": True, "segs": segs, "seconds": 1800.0, "took": 150.0}, posts)
    assert media.transcribe_pending(INDEX, now=NOW) == {"transcribed": 1, "new": 1, "pending": 0, "failed": [], "off": False}
    assert posts == [("http://stt:8320/transcribe", {"url": ep["audio_url"]}, {"x-ffl-job": "job-secret"})]
    item = items_of("pod:LAR")[0]
    assert item["ext_id"] == f"{ep['guid']}#3" and item["url"] == ep["url"] and item["at_s"] == 0.0  # 30 s before the name
    assert item["text"] == "Matthew Stafford was sharp. Agreed."
    assert db.news_media(ep["guid"])["segs"] == segs
    assert db.get_state("news_pending_audio") == {}
    assert media.transcribe_pending(INDEX, now=NOW)["transcribed"] == 0 and len(posts) == 1  # nothing re-sent
    media.run_all(INDEX, now=NOW + timedelta(hours=2))
    assert db.get_state("news_pending_audio") == {}, "a stored episode never queues again"


def test_transcribe_pending_service_off_errors_and_expiry(monkeypatch):
    db.put_news_sources([POD])
    entry = {"source_id": "pod:LAR", "club": "LAR", "title": "t", "url": "https://omny.test/ep",
             "audio_url": "https://cdn.test/ep.mp3"}
    db.put_state("news_pending_audio", {"new": {**entry, "published_at": "2026-10-15T22:59:00+00:00"},
                                        "old": {**entry, "published_at": "2026-10-01T22:59:00+00:00"}})
    posts = []
    _post(monkeypatch, {"ok": True, "segs": [], "seconds": 1.0, "took": 1.0}, posts)
    # no FFL_STT_URL: nothing is sent; the stale entry is dropped
    assert media.transcribe_pending(INDEX, now=NOW) == {"transcribed": 0, "new": 0, "pending": 1, "failed": [], "off": True}
    assert posts == [] and list(db.get_state("news_pending_audio")) == ["new"]
    # the service is not there (the profile is off): the episode waits, and that costs it no try
    monkeypatch.setenv("FFL_STT_URL", "http://stt:8320")
    _post(monkeypatch, media.requests.ConnectionError("refused"))
    assert media.transcribe_pending(INDEX, now=NOW)["off"] is True
    assert db.get_state("news_pending_audio")["new"].get("tries", 0) == 0
    # an error from the service is this episode's: tried STT_TRIES times, then dropped
    for n in range(1, media.STT_TRIES + 1):
        _post(monkeypatch, {"ok": False, "error": "DecodeError: not audio"})
        got = media.transcribe_pending(INDEX, now=NOW)
        assert got["failed"] == ["pod:LAR"] and got["off"] is False, n
        assert db.get_state("news_pending_audio")["new"]["tries"] == n
    assert media.transcribe_pending(INDEX, now=NOW) == {"transcribed": 0, "new": 0, "pending": 0, "failed": [], "off": False}
    assert db.get_state("news_pending_audio") == {}


def test_a_feed_with_transcripts_never_queues(monkeypatch):
    """An Omny episode with its transcript takes the old path even with an stt: no audio is sent anywhere."""
    db.put_news_sources([POD])
    monkeypatch.setenv("FFL_STT_URL", "http://stt:8320")
    eps = media.parse_podcast_feed(fix("omny.xml"), POD)
    srt = b"1\n00:00:01,000 --> 00:00:02,000\nHello.\n"
    serve(monkeypatch, {POD["url"]: fix("omny.xml"), **{e["transcript_url"]: srt for e in eps}})
    assert media.run_all(INDEX, now=NOW)["media"] == 1
    assert db.get_state("news_pending_audio") == {}
    with pytest.raises(AssertionError):
        _post(monkeypatch, {"ok": True}); assert media.transcribe_pending(INDEX, now=NOW)["pending"] == 1
