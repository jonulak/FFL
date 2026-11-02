"""A vector for each written story's words (headline, overview, a line per player), for GET /news/search.

Runs after the write stage: once per written revision (a rewrite clears the vector, `db.put_story_words`), and on its
first run it backfills the stories written before. A model failure fails this stage alone; the next run retries.
"""
from __future__ import annotations

from .. import db, embed

BATCH = 200  # stories a run: a backfill spreads over a few runs instead of holding one up


def text(s: dict) -> str:
    return "\n".join([s["headline"], s["overview"], *[p["reason"] for p in s["players"] if p.get("reason")]])


def run() -> dict:
    stories = db.unembedded_stories(BATCH)
    vecs = embed.passages([text(s) for s in stories]) if stories else []
    stored = sum(db.put_story_embedding(s["id"], s["headline"], v) for s, v in zip(stories, vecs))
    return {"embedded": stored, "stale": len(stories) - stored}
