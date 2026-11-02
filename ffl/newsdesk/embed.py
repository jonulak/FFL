"""Vectors for GET /news/search: each written story's words (headline, overview, a line per player), and each source
item's latest text in pieces of about 300-450 tokens, so a detail only a source mentions is found too.

The run's last stage before health, so it never delays a push: once per written revision (a rewrite clears the vector,
`db.put_story_words`) and per item text (a revision is chunked again, `db.unchunked_items`). Its first run backfills
everything before (a season, ~14k pieces, took 12.5 minutes and 640 MB on a Mac). A model failure fails this
stage alone; what was stored stays, and the next run does the rest.
"""
from __future__ import annotations

import re
import textwrap

from .. import db, embed

BATCH = 256    # stories, or items, embedded and stored at a time: a backfill's vectors never sit in memory all at once
MAX = 1800     # chars a piece: ~450 tokens, inside bge-small's 512 with the title
OVERLAP = 300  # a piece starts with the previous one's last paragraph when it is this short


def text(s: dict) -> str:
    return "\n".join([s["headline"], s["overview"], *s["reasons"]])


def _fit(para: str) -> list[str]:
    """A paragraph as parts of at most MAX chars: whole sentences where they fit, a sentence over MAX cut at spaces."""
    if len(para) <= MAX:
        return [para]
    out: list[str] = []
    for sent in re.split(r"(?<=[.!?])\s+", para):
        for part in textwrap.wrap(sent, MAX) if len(sent) > MAX else [sent]:
            if out and len(out[-1]) + 1 + len(part) <= MAX:
                out[-1] += " " + part
            else:
                out.append(part)
    return out


def chunks(body: str) -> list[str]:
    """The text in pieces of at most MAX chars on paragraph boundaries, most articles one or two pieces."""
    out: list[str] = []
    cur: list[str] = []
    for para in (part for line in body.split("\n") if (p := line.strip()) for part in _fit(p)):
        if cur and len("\n".join(cur)) + 1 + len(para) > MAX:
            out.append("\n".join(cur))
            last = cur[-1]
            cur = [last] if len(last) <= OVERLAP and len(last) + 1 + len(para) <= MAX else []
        cur.append(para)
    if cur:
        out.append("\n".join(cur))
    return out


def _stories() -> dict:
    stories, stored = db.unembedded_stories(), 0
    for at in range(0, len(stories), BATCH):
        batch = stories[at:at + BATCH]
        vecs = embed.passages([text(s) for s in batch])
        stored += sum(db.put_story_embedding(s["id"], s["headline"], v) for s, v in zip(batch, vecs))
    return {"embedded": stored, "stale": len(stories) - stored}


def _items() -> dict:
    items = pieces = 0
    while batch := db.unchunked_items(BATCH):
        cut = [chunks(i["text"]) or [""] for i in batch]  # an empty text still gets a row (its title), so it isn't read again
        vecs = iter(embed.passages([f"{i['title']}\n{p}" for i, ps in zip(batch, cut) for p in ps]))
        stored = 0
        for i, ps in zip(batch, cut):
            got = [(p, next(vecs)) for p in ps]
            if db.put_item_chunks(i["id"], i["hash"], got):
                stored, pieces = stored + 1, pieces + len(ps)
        items += stored
        if not stored:  # every item in the batch was revised meanwhile: the next run takes them
            break
    return {"chunked_items": items, "chunks": pieces}


def run() -> dict:
    return {**_stories(), **_items()}
