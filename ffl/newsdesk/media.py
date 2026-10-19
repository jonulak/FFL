"""Press conferences off the clubs' YouTube channels and episodes of their official podcasts.

Each transcript is stored whole (`news_media`, so matching and tagging can be re-run without refetching),
then `split.by_player` cuts it into one item per player named, linking to that moment: a YouTube `&t=`,
or the episode page with the seconds in `at_s`. A transcript stored is done: one naming nobody is not
fetched again.

YouTube: the channel's RSS lists its newest 15 videos; a title `is_presser` is fetched through
youtube-transcript-api (English, manual or automatic). Captions come some time after the upload: a
presser without them yet waits in state `news_pending_media` `{video_id: first_seen}` and is retried
for PENDING, at most once per RETRY_EVERY (its last try in state `news_pending_tried`). Expired entries
are kept, not deleted, since they are how often captions never came; when a pending video's captions
arrive their lag (published to fetched, minutes) is appended to state `news_caption_lag` (captions there
on the first read are no lag: the video may simply have been seen late). YouTube rate-limits caption reads
(one HTTP 429 in testing), so they are GAP_S apart and at most CAPTIONS_PER_RUN a run: new videos first,
newest first, then pending ones in the slots left, so a pile of never-captioned live-stream pages cannot
starve a new presser. A blocked read stops the run's captions and fails its source, never reading as
"no captions yet".

Podcasts: Omny feeds carry `<podcast:transcript>` in SubRip, WebVTT and plain text; SubRip is taken.
A feed lists every episode ever published (NE's is 0.9 MB gzipped), so each is polled at most once per
PODCAST_EVERY, conditionally on the ETag / Last-Modified it last gave (state `news_podcast_polled`
`{source id: {at, etag, modified}}`). The validators are kept only when every episode of that poll was
read: after a failed transcript fetch the next poll takes the whole feed again.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests
from youtube_transcript_api import (NoTranscriptFound, RequestBlocked, TranscriptsDisabled, VideoUnplayable,
                                    YouTubeRequestFailed, YouTubeTranscriptApi)

from .. import db
from . import collect, split
from .players import Index

YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={id}"
YT_WATCH = "https://www.youtube.com/watch?v={id}"
PRESSER = re.compile(r"press conference|presser|postgame|media availability|podium|availability|speaks to"
                     r"|talks to the media|meets with the media|address(?:es)? the media|\bpreviews\b|\btalks\b", re.I)
# "<Name> on <topic>" at the start, a position or title first: "QB Bo Nix on Denver's 4-2 start", "Derrick Henry On Loss…"
_NAME = r"[A-Z](?:[a-z]|\.[A-Z])[\w.'’\-]*"  # "Nix", "Ja’Quan", "J.J."; never an all-caps word
NAME_ON = re.compile(rf"(?:[A-Z/]{{1,4}} )?{_NAME}(?: {_NAME}){{1,3}} (?i:on) ")
PODCAST_EVERY = timedelta(hours=1)
LANGS = ("en", "en-US", "en-GB")
GAP_S = 2.0
CAPTIONS_PER_RUN = 10
PENDING = timedelta(hours=24)
RETRY_EVERY = timedelta(hours=1)
TRANSCRIPT_TYPES = ("application/srt", "application/x-subrip", "text/vtt", "text/plain")  # preference order
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_POD = "{https://podcastindex.org/namespace/1.0}"
_CUE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})\s*-->")
_TAG = re.compile(r"<[^>]+>")  # WebVTT's <v Speaker 1>, <i>
_SPEAKER = re.compile(r"^Speaker \d+:\s*")  # Omny's SubRip label
_LAST = [0.0]  # monotonic time of the last caption read


def _get(url: str, **kw) -> requests.Response:
    return requests.get(url, timeout=collect.TIMEOUT, **kw)


def _xml(body: str | bytes) -> ET.Element:
    return ET.fromstring(body.encode() if isinstance(body, str) else body)  # a str with an encoding declaration is refused


def is_presser(title: str) -> bool:
    return bool(PRESSER.search(title) or NAME_ON.match(title))


def parse_channel_feed(xml: str | bytes, source: dict) -> list[dict]:
    out = []
    for e in _xml(xml).iter(_ATOM + "entry"):
        vid, when = e.findtext(_YT + "videoId"), e.findtext(_ATOM + "published")
        if vid and when:
            out.append({"video_id": vid, "title": (e.findtext(_ATOM + "title") or "").strip(),
                        "published_at": collect._iso(when.strip())})
    return out


def parse_podcast_feed(xml: str | bytes, source: dict) -> list[dict]:
    """Every episode; `transcript_url` is None while the feed has none for it (Omny adds it after publishing)."""
    out = []
    for it in _xml(xml).iter("item"):
        guid, when = (it.findtext("guid") or "").strip(), it.findtext("pubDate")
        if not guid or not when:
            continue
        found = {t.get("type"): t.get("url") for t in it.findall(_POD + "transcript") if t.get("url")}
        kind = next((t for t in TRANSCRIPT_TYPES if t in found), next(iter(found), None))
        enclosure = it.find("enclosure")
        url = (it.findtext("link") or "").strip() or (enclosure.get("url") if enclosure is not None else "")
        out.append({"guid": guid, "title": (it.findtext("title") or "").strip(),
                    "published_at": collect._utc(parsedate_to_datetime(when.strip())), "url": url,
                    "transcript_url": found.get(kind), "transcript_type": kind})
    return out


def parse_transcript(body: str, type: str) -> list[dict]:
    """SubRip and WebVTT by cue, tags and speaker labels dropped; anything else is one seg at 0."""
    if not any(k in (type or "").lower() for k in ("srt", "subrip", "vtt")):
        text = " ".join(body.split())
        return [{"start": 0.0, "text": text}] if text else []
    out = []
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n")):
        lines = block.strip().split("\n")
        for i, line in enumerate(lines):
            m = _CUE.match(line.strip())
            if not m:
                continue
            h, mi, s, ms = m.groups()
            text = " ".join(" ".join(_SPEAKER.sub("", _TAG.sub("", ln)).strip() for ln in lines[i + 1:]).split())
            if text:
                out.append({"start": round(int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000, 3), "text": text})
            break
    return out


def captions(video_id: str) -> list[dict] | None:
    """The video's English captions, None when it has none yet. Raises when YouTube refuses the read."""
    wait = _LAST[0] + GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    try:
        got = YouTubeTranscriptApi().fetch(video_id, languages=LANGS)
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnplayable):  # unplayable: still live, or processing
        return None
    finally:
        _LAST[0] = time.monotonic()
    return [{"start": round(float(s.start), 3), "text": " ".join(s.text.split())} for s in got.snippets if s.text.strip()]


def items_for(media: dict, segs: list[dict], index: Index, clubs: tuple[str, ...]) -> list[dict]:
    """One item per player the transcript names: ext_id `<media id>#<player id>`, his excerpt as the text."""
    youtube = media["source_id"].startswith("yt:")
    out = []
    for part in split.by_player(segs, index, clubs, media["title"]):
        url = f"{YT_WATCH.format(id=media['id'])}&t={int(part['start'])}s" if youtube else media["url"]
        it = collect._item({"id": media["source_id"]}, f"{media['id']}#{part['player_id']}", url,
                           media["published_at"], media["title"], part["text"], clubs[0] if clubs else None)
        out.append({**it, "at_s": part["start"]})
    return out


def _store(media: dict, segs: list[dict], index: Index, club: str | None, at: str) -> int:
    """The whole transcript, then its per-player items. Returns how many items were new."""
    db.put_news_media({**media, "segs": segs, "fetched_at": at})
    items = items_for(media, segs, index, (club,) if club else ())
    return len(db.add_news_items([{**i, "fetched_at": at, "hash": collect.item_hash(i["title"], i["text"])}
                                  for i in items]))


def _poll(source: dict, last: dict | None = None) -> tuple[list[dict], dict | None] | Exception:
    """(rows, validators) off the feed; a 304 against `last`'s validators is no rows and `last`'s kept."""
    headers = {}
    if last and last.get("etag"):
        headers["If-None-Match"] = last["etag"]
    if last and last.get("modified"):
        headers["If-Modified-Since"] = last["modified"]
    try:
        r = _get(source["url"], headers=headers) if headers else _get(source["url"])
        if r.status_code == 304:
            return [], {"etag": last.get("etag"), "modified": last.get("modified")}
        r.raise_for_status()
        parse = parse_channel_feed if source["kind"] == "youtube" else parse_podcast_feed
        return parse(r.content, source), {"etag": r.headers.get("ETag"), "modified": r.headers.get("Last-Modified")}
    except (requests.RequestException, ET.ParseError, ValueError) as e:
        return e


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


def run_all(index: Index, now: datetime | None = None) -> dict:
    """Every active media source: presser videos and podcast episodes of the last three days not yet
    stored, each stored whole and split per player. Marks each source's health."""
    now = now or datetime.now(timezone.utc)
    at = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    cutoff = collect._utc(now - collect.MAX_AGE)
    last_poll: dict[str, dict] = db.get_state("news_podcast_polled", {}) or {}

    def due(s: dict) -> bool:
        seen = last_poll.get(s["id"])
        return s["kind"] != "podcast" or not seen or now - datetime.fromisoformat(seen["at"]) >= PODCAST_EVERY

    sources = [s for s in db.news_sources() if s["kind"] in collect.MEDIA_KINDS and due(s)]
    with ThreadPoolExecutor(collect.WORKERS) as pool:
        got = dict(zip([s["id"] for s in sources], pool.map(lambda s: _poll(s, last_poll.get(s["id"])), sources)))
    errors = {sid: _err(g) for sid, g in got.items() if isinstance(g, Exception)}
    polled = {sid: g[0] for sid, g in got.items() if sid not in errors}
    new = stored = 0

    def fresh(rows: list[dict], key: str) -> list[dict]:
        return [r for r in rows if r["published_at"] >= cutoff and db.news_media(r[key]) is None]

    for source in sources:
        if source["kind"] != "podcast" or source["id"] in errors:
            continue
        for ep in fresh(polled[source["id"]], "guid"):
            if not ep["transcript_url"]:
                continue
            try:
                r = _get(ep["transcript_url"])
                r.raise_for_status()
                segs = parse_transcript(r.content.decode("utf-8", "replace"), ep["transcript_type"])
                row = {"id": ep["guid"], "source_id": source["id"], "url": ep["url"], "title": ep["title"],
                       "published_at": ep["published_at"]}
                new += _store(row, segs, index, source["club"], at)
                stored += 1
            except (requests.RequestException, db.DBError) as e:
                errors[source["id"]] = _err(e)
        if source["id"] in polled:  # a failed episode drops the validators, so the next poll reads it again
            kept = got[source["id"]][1] if source["id"] not in errors else {}
            last_poll[source["id"]] = {"at": at, **(kept or {})}
    db.put_state("news_podcast_polled", last_poll)

    pending: dict[str, str] = db.get_state("news_pending_media", {}) or {}
    tried: dict[str, str] = db.get_state("news_pending_tried", {}) or {}
    lag: list[float] = db.get_state("news_caption_lag", []) or []

    def waiting(vid: str) -> bool:
        return now - datetime.fromisoformat(pending[vid]) <= PENDING

    def retry_due(vid: str) -> bool:
        return waiting(vid) and (vid not in tried or now - datetime.fromisoformat(tried[vid]) >= RETRY_EVERY)

    videos = [(source, v) for source in sources if source["kind"] == "youtube" and source["id"] not in errors
              for v in fresh(polled[source["id"]], "video_id") if is_presser(v["title"])]
    videos.sort(key=lambda sv: sv[1]["published_at"], reverse=True)
    # new videos first; pending ones fill what is left of the budget, each at most once per RETRY_EVERY
    videos = ([sv for sv in videos if sv[1]["video_id"] not in pending]
              + [sv for sv in videos if sv[1]["video_id"] in pending and retry_due(sv[1]["video_id"])])
    for source, v in videos[:CAPTIONS_PER_RUN]:
        vid = v["video_id"]
        try:
            segs = captions(vid)
        except (RequestBlocked, YouTubeRequestFailed, requests.RequestException) as e:  # 429, a blocked IP, no network
            errors[source["id"]] = _err(e)
            break
        except Exception as e:  # noqa: BLE001 — this video's own problem (unlisted, age-gated…): the rest still run
            errors[source["id"]] = _err(e)
            continue
        if segs is None:
            pending.setdefault(vid, at)
            tried[vid] = at
            continue
        row = {"id": vid, "source_id": source["id"], "url": YT_WATCH.format(id=vid), "title": v["title"],
               "published_at": v["published_at"]}
        try:
            new += _store(row, segs, index, source["club"], at)
        except db.DBError as e:
            errors[source["id"]] = _err(e)
            continue
        stored += 1
        tried.pop(vid, None)
        if pending.pop(vid, None) is not None:  # captions on a first read say nothing of how long they took
            lag.append(round((now - datetime.fromisoformat(v["published_at"])).total_seconds() / 60, 1))

    db.put_state("news_pending_media", pending)
    db.put_state("news_pending_tried", {vid: t for vid, t in tried.items() if vid in pending and waiting(vid)})
    db.put_state("news_caption_lag", lag)
    for source in sources:
        try:
            db.mark_news_source(source["id"], source["id"] not in errors, errors.get(source["id"]), at)
        except db.DBError as e:
            print(f"news source {source['id']} not marked: {e}")
    return {"new": new, "media": stored, "pending": sum(1 for vid in pending if waiting(vid)),
            "failed": sorted(errors)}
