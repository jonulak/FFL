"""A vector for each written story's words (headline, overview, a line per player), for GET /news/search.

The run's last stage before health, so it never delays a push: once per written revision (a rewrite clears the vector,
`db.put_story_words`), and on its first run it backfills every story written before (about 17 ms each, a season in a
minute or two). A model failure fails this stage alone; what was stored stays, and the next run does the rest.
"""
from __future__ import annotations

from .. import db, embed

CHUNK = 256  # stories embedded and stored at a time: a backfill's vectors never sit in memory all at once


def text(s: dict) -> str:
    return "\n".join([s["headline"], s["overview"], *s["reasons"]])


def run() -> dict:
    stories, stored = db.unembedded_stories(), 0
    for at in range(0, len(stories), CHUNK):
        chunk = stories[at:at + CHUNK]
        vecs = embed.passages([text(s) for s in chunk])
        stored += sum(db.put_story_embedding(s["id"], s["headline"], v) for s, v in zip(chunk, vecs))
    return {"embedded": stored, "stale": len(stories) - stored}
