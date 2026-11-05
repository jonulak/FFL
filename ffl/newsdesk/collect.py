"""Text collectors: team sites, ESPN team news, Pro Football Talk, Rotowire and Bluesky into `news_items`.

`poll` reads one source's feed; `run_all` stores what is new and marks each source's health; `recheck`
looks again at the last 48 h for edits and deletions. Only items published in the last three days are
collected: older ones are past the recheck window.

Edits: pft, Bluesky and Rotowire carry their whole text in the feed, so a re-poll compares its hash with
the stored one. A team-site or ESPN item stores the article body, which the feed does not carry, so a
re-poll compares the title only (a changed one refetches the body); `recheck` fetches the page of an item
that fell off its feed (404/410 withdraws it). Items still in the feed are never fetched again.

Rotowire (ESPN's fantasy news feed): it answers only per player (no playerId is a 500, playerId=0 an
empty feed; checked 2026-10-19), so the one `rotowire` source polls every rostered player and the
best ROTOWIRE_FA free agents (by the pool's `add`, else ESPN's percent owned), ids off the Players
page's stored pool (page_cache "waivers"), else `espn_season.free_agent_pool`, with a 30-min cache of the raw
document. This is the outlook's only Rotowire: its sheet reads the desk's stories.

ESPN team news lists league-wide pieces (betting lines, highlights) under every club; an article is kept
by a club's source only when it is categorized under that club and at most one other, and its body
comes from the content API the article links (www.espn.com answers 403, or an empty 202, to a script).
"""
from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from .. import db, news
from .players import Index

MEDIA_KINDS = {"youtube", "podcast"}
BODY_KINDS = {"site", "espn"}  # the item's text is the article page's, not the feed's
MAX_AGE = timedelta(days=3)
RECHECK = timedelta(hours=48)
TIMEOUT = 20
WORKERS = 8
BSKY = "https://public.api.bsky.app/xrpc/"
BSKY_LIMIT = 30
BSKY_BATCH = 25  # getPosts takes at most 25 uris
ESPN_STORY = "https://content.core.api.espn.com/v1/sports/news/{id}"
ESPN_MAX_CLUBS = 2
ROTO_S = 1800
ROTOWIRE_FA = 150  # free agents polled beside every rostered player
ROTOWIRE_WORKERS = 4
GONE = {404, 410}
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
_SEEN: dict[str, set[str]] = {}  # source id -> the ext_ids its last good poll listed (recheck's "still in the feed")
_ROTO: dict[int, tuple[float, dict]] = {}  # espn id -> (expires_at, raw feed)


def _get(url: str, **kw) -> requests.Response:
    # requests' default User-Agent: site.api.espn.com answers 403 to a browser-shaped one, and the rest take it
    return requests.get(url, timeout=TIMEOUT, **kw)


def item_hash(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()


def _utc(dt: datetime) -> str:
    """A naive time is UTC, never local: parsedate_to_datetime returns one for RFC 5322's -0000 (UTC, with no
    claim about the sender's zone)."""
    return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc).isoformat()


def _iso(s: str) -> str:
    return _utc(datetime.fromisoformat(s.replace("Z", "+00:00")))


def _item(source: dict, ext_id: str, url: str, published_at: str, title: str, text: str,
          club: str | None = None) -> dict:
    return {"source_id": source["id"], "ext_id": ext_id, "url": url, "published_at": published_at,
            "title": title.strip(), "text": text.strip(), "club": club, "at_s": None}


def article_text(html: str | bytes) -> str:
    """The body's paragraphs, one blank line apart. NFL club sites wrap each in `nfl-c-body-part--text`;
    elsewhere an `<article>`'s paragraphs, else every paragraph, else the bare text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside", "iframe"]):
        tag.decompose()
    paras = (soup.select(".nfl-c-body-part--text p") or soup.select("article p") or soup.find_all("p"))
    out = [" ".join(p.get_text().split()) for p in paras]  # no separator: a link inside a sentence keeps its spacing
    out = [p for p in out if p]
    return "\n\n".join(out) if out else " ".join(soup.get_text(" ", strip=True).split())


def parse_rss(xml: str | bytes, source: dict) -> list[dict]:
    out = []
    for it in ET.fromstring(xml).iter("item"):
        link = (it.findtext("link") or "").strip()
        ext = (it.findtext("guid") or "").strip() or link
        when = it.findtext("pubDate")
        if not ext or not when:
            continue
        body = it.findtext(_CONTENT) or it.findtext("description") or ""
        out.append(_item(source, ext, link, _utc(parsedate_to_datetime(when.strip())), it.findtext("title") or "",
                         article_text(body) if body.strip() else "", source.get("club")))
    return out


def _quoted(post: dict) -> str:
    """A quote post's quoted text (a quote with media nests the record one level deeper)."""
    emb = post.get("embed") or {}
    rec = emb.get("record") or {}
    rec = rec.get("record", rec) if emb.get("$type", "").startswith("app.bsky.embed.recordWithMedia") else rec
    return ((rec.get("value") or {}).get("text") or "").strip()


def _bsky_item(post: dict, source: dict) -> dict | None:
    rec = post.get("record") or {}
    own = (rec.get("text") or "").strip()
    if not own:
        return None
    quoted = _quoted(post)
    handle = (post.get("author") or {}).get("handle") or source["id"].split(":", 1)[1]
    url = f"https://bsky.app/profile/{handle}/post/{post['uri'].rsplit('/', 1)[1]}"
    return _item(source, post["uri"], url, _iso(rec["createdAt"]), own.split("\n", 1)[0][:140],
                 own + (f"\n\n> {quoted}" if quoted else ""), source.get("club"))


def parse_bluesky(feed: dict, source: dict) -> list[dict]:
    """The author's own posts, replies and quotes; reposts are someone else's words and arrive twice."""
    out = []
    for entry in feed.get("feed") or []:
        if (entry.get("reason") or {}).get("$type", "").endswith("reasonRepost"):
            continue
        it = _bsky_item(entry["post"], source)
        if it:
            out.append(it)
    return out


def _team_id(source: dict) -> int | None:
    team = parse_qs(urlparse(source["url"]).query).get("team")
    return int(team[0]) if team else None


def parse_espn_news(doc: dict, source: dict) -> list[dict]:
    """Stories categorized under the source's club and at most one other; video clips are skipped.
    The text is the summary until the story's body is fetched."""
    team, out = _team_id(source), []
    for a in doc.get("articles") or []:
        clubs = {c["teamId"] for c in a.get("categories") or [] if c.get("teamId")}
        if a.get("type") == "Media" or team not in clubs or len(clubs) > ESPN_MAX_CLUBS:
            continue
        web = ((a.get("links") or {}).get("web") or {}).get("href")
        if not web or not a.get("published"):
            continue
        out.append(_item(source, str(a["id"]), web, _iso(a["published"]), a.get("headline") or "",
                         a.get("description") or "", source.get("club")))
    return out


def parse_rotowire(doc: dict, source: dict, index: Index | None = None) -> list[dict]:
    """One player's Rotowire blurbs (ESPN's own stories share the feed and are skipped); the club is the
    player's, from the index, so a bare surname in the blurb can match him."""
    out = []
    for it in doc.get("feed") or []:
        pid = it.get("playerId")
        if it.get("type") != "Rotowire" or not pid or not it.get("published"):
            continue
        club = (index.by_id.get(pid) or {}).get("team") if index else None
        out.append(_item(source, str(it["id"]), news.PLAYER_PAGE.format(pid=pid), _iso(it["published"]),
                         it.get("headline") or "", it.get("story") or "", club))
    return out


def _rotowire_ids(index: Index | None = None) -> list[int]:
    """`_league_rotowire_ids` of every league, each player once."""
    from .. import leagues
    out: list[int] = []
    for ref in leagues.joined():
        out += _league_rotowire_ids(ref, index)
    return sorted(set(out))


def _league_rotowire_ids(ref, index: Index | None) -> list[int]:
    """A league's rostered players, its top ROTOWIRE_FA free agents and its watched players, off its owner's stored
    Players pool, else built."""
    pool = next((p["payload"] for p in db.pages(ref=ref) if p["key"] == "waivers"), None)
    if pool is None:
        from ..espn_season import free_agent_pool  # espn_season imports a lot; keep the collectors importable alone
        pool = free_agent_pool(ref=ref)

    def ids(rows):
        return [r["player_id"] for r in rows if r.get("pos") != "D/ST" and (r.get("player_id") or 0) > 0]
    rostered = set(ids(r for rs in (pool.get("rostered") or {}).values() for r in rs))
    owned = index.owned if index else {}
    frees = sorted((r for rs in (pool.get("positions") or {}).values() for r in rs),
                   key=lambda r: (r.get("add") is None, -(r.get("add") or 0), -owned.get(r.get("player_id"), 0)))
    top = [pid for pid in dict.fromkeys(ids(frees)) if pid not in rostered][:ROTOWIRE_FA]
    try:
        from .. import leagues  # every member's watchlist; a watched D/ST has no Rotowire feed
        watched = {pid for m in leagues.members(ref.key) for pid in db.watched_ids(ref=m) if pid > 0}
    except db.DBError as e:
        print(f"news: watched ids unreadable, polling without them: {e}")
        watched = set()
    return sorted(rostered | set(top) | watched)


def _rotowire_doc(pid: int) -> dict | None:
    """One player's raw feed, cached 30 min; the last good one (or None) when the call fails."""
    hit = _ROTO.get(pid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        r = _get(news.ROTOWIRE, params={"playerId": pid, "limit": news.ROTOWIRE_LIMIT})
        r.raise_for_status()
        doc = r.json()
    except (requests.RequestException, ValueError):
        return hit[1] if hit else None
    _ROTO[pid] = (time.time() + ROTO_S, doc)
    return doc


def _poll_rotowire(source: dict, index: Index | None) -> list[dict]:
    ids = _rotowire_ids(index)
    with ThreadPoolExecutor(ROTOWIRE_WORKERS) as pool:
        docs = [d for d in pool.map(_rotowire_doc, ids) if d is not None]
    if ids and not docs:
        raise RuntimeError(f"no Rotowire feed answered for {len(ids)} players")
    # a blurb about two players sits in both feeds under one id
    return list({i["ext_id"]: i for d in docs for i in parse_rotowire(d, source, index)}.values())


def poll(source: dict, index: Index | None = None) -> list[dict]:
    """One source's current feed. Raises on an HTTP or parse failure."""
    kind = source["kind"]
    if kind == "rotowire":
        return _poll_rotowire(source, index)
    if kind == "bluesky":
        r = _get(BSKY + "app.bsky.feed.getAuthorFeed",
                 params={"actor": source["id"].split(":", 1)[1], "limit": BSKY_LIMIT})
    else:
        r = _get(source["url"])
    r.raise_for_status()
    if kind in ("site", "pft"):
        return parse_rss(r.content, source)
    if kind == "espn":
        return parse_espn_news(r.json(), source)
    if kind == "bluesky":
        return parse_bluesky(r.json(), source)
    raise ValueError(f"no collector for kind {kind!r}")


class _Gone(Exception):
    pass


def _body(kind: str, ext_id: str, url: str) -> str | None:
    """The article's body (site: the page; espn: the content API), None when it cannot be read.
    Raises `_Gone` on a 404/410."""
    r = _get(ESPN_STORY.format(id=ext_id) if kind == "espn" else url)
    if r.status_code in GONE:
        raise _Gone(r.status_code)
    r.raise_for_status()
    html = ((r.json().get("headlines") or [{}])[0].get("story") or "") if kind == "espn" else r.content
    return (article_text(html) or None) if html else None


def _with_body(it: dict, kind: str, fallback: str) -> dict:
    """The item with its article body; `fallback` when the page cannot be read."""
    try:
        text = _body(kind, it["ext_id"], it["url"])
    except (_Gone, requests.RequestException, ValueError):
        text = None
    return {**it, "text": text or fallback}


def _collect(source: dict, index: Index, window: dict, cutoff: str, at: str) -> tuple[int, int]:
    """Poll one source and store it: (new, edited). Raises when the poll fails."""
    sid, kind = source["id"], source["kind"]
    items = poll(source, index)
    _SEEN[sid] = {i["ext_id"] for i in items}
    items = [i for i in items if i["published_at"] >= cutoff]
    stored = db.news_item_hashes(sid, [i["ext_id"] for i in items])
    fresh = [i for i in items if i["ext_id"] not in stored]
    edits = []
    for it in items:
        old = window.get((sid, it["ext_id"]))
        if it["ext_id"] not in stored or old is None:
            continue
        if kind in BODY_KINDS:
            if it["title"] != old["title"]:
                edits.append((stored[it["ext_id"]][0], _with_body(it, kind, old["text"])))
        elif item_hash(it["title"], it["text"]) != old["hash"]:
            edits.append((stored[it["ext_id"]][0], it))
    if kind in BODY_KINDS:
        fresh = [_with_body(i, kind, i["text"]) for i in fresh]  # the feed's summary when the page fails
    added = db.add_news_items([{**i, "fetched_at": at, "hash": item_hash(i["title"], i["text"])} for i in fresh])
    edited = 0
    for item_id, it in edits:
        h = item_hash(it["title"], it["text"])
        if h != window[(sid, it["ext_id"])]["hash"]:
            db.update_item_text(item_id, it["title"], it["text"], h, at)
            edited += 1
    return len(added), edited


def run_all(index: Index, now: datetime | None = None) -> dict:
    """Every active text source: poll, store the new items, compare the known ones, mark the source's
    health. One source's failure never stops the rest."""
    now = now or datetime.now(timezone.utc)
    at = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    window = {(r["source_id"], r["ext_id"]): r for r in db.recheck_items(_utc(now - RECHECK))}
    cutoff = _utc(now - MAX_AGE)
    sources = [s for s in db.news_sources() if s["kind"] not in MEDIA_KINDS]

    def one(source: dict) -> tuple[str, int, int, str | None]:
        try:
            new, edited = _collect(source, index, window, cutoff, at)
        # any failure is this source's, reported on it; SystemExit is a league's missing settings (Rotowire's pools)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            _SEEN.pop(source["id"], None)
            err = f"{type(e).__name__}: {e}"[:300]
            try:
                db.mark_news_source(source["id"], False, err, at)
            except db.DBError as de:
                print(f"news source {source['id']} not marked: {de}")
            return source["id"], 0, 0, err
        db.mark_news_source(source["id"], True, None, at)
        return source["id"], new, edited, None

    with ThreadPoolExecutor(WORKERS) as pool:
        done = list(pool.map(one, sources))
    return {"new": sum(d[1] for d in done), "edited": sum(d[2] for d in done),
            "failed": sorted(d[0] for d in done if d[3])}


def _bsky_posts(uris: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(uris), BSKY_BATCH):
        r = _get(BSKY + "app.bsky.feed.getPosts", params={"uris": uris[i:i + BSKY_BATCH]})
        r.raise_for_status()
        out.update({p["uri"]: p for p in r.json().get("posts") or [] if isinstance(p, dict) and p.get("uri")})
    return out


def recheck(now: datetime) -> dict:
    """The last 48 h's items that fell off their source's last good poll: a Bluesky post getPosts no
    longer returns, or an article answering 404/410, is withdrawn; a changed text is an edit. An item
    still in its feed was compared by `run_all`; a source whose poll failed is left alone (no way to
    tell), and so is Rotowire (a blurb has no page of its own)."""
    at = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    kinds = {s["id"]: s["kind"] for s in db.news_sources(active_only=False)}
    gone = [r for r in db.recheck_items(_utc(now - RECHECK))
            if r["source_id"] in _SEEN and r["ext_id"] not in _SEEN[r["source_id"]]]
    edited, withdrawn, retracted = 0, 0, []

    def withdraw(row: dict) -> None:
        nonlocal withdrawn
        story = db.withdraw_item(row["id"], at)
        withdrawn += 1
        if story:
            retracted.append(story["id"])

    def edit(row: dict, title: str, text: str) -> None:
        nonlocal edited
        h = item_hash(title, text)
        if h != row["hash"]:
            db.update_item_text(row["id"], title, text, h, at)
            edited += 1

    posts = [r for r in gone if kinds.get(r["source_id"]) == "bluesky"]
    if posts:
        try:
            live = _bsky_posts([r["ext_id"] for r in posts])
        except (requests.RequestException, ValueError) as e:
            print(f"news recheck: Bluesky getPosts failed: {e}")
        else:
            for row in posts:
                post = live.get(row["ext_id"])
                try:
                    it = _bsky_item(post, {"id": row["source_id"], "club": row["club"]}) if post else None
                except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
                    # a post in a shape we cannot read is still there: left alone, never taken for deleted
                    print(f"news recheck: Bluesky post {row['ext_id']}: {type(e).__name__}: {e}")
                    continue
                if it is None:
                    withdraw(row)
                else:
                    edit(row, it["title"], it["text"])
    for row in gone:
        kind = kinds.get(row["source_id"])
        if kind not in ("site", "espn", "pft"):
            continue
        try:
            if kind == "pft":  # its text is the feed's, so the page only says whether it is still there
                if _get(row["url"]).status_code in GONE:
                    withdraw(row)
                continue
            text = _body(kind, row["ext_id"], row["url"])
        except _Gone:
            withdraw(row)
            continue
        except (requests.RequestException, ValueError) as e:
            print(f"news recheck: {row['url']}: {e}")
            continue
        if text:
            edit(row, row["title"], text)
    return {"edited": edited, "withdrawn": withdrawn, "retracted": retracted}
