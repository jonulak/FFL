"""The story search's eval: 100 written stories (20 situations x 5 fictional players), 5 queries worded unlike the
stories, each expecting its situation's 5; and 12 detail queries whose answer is only in one story's source item
(a fictional practice report, the detail buried among filler paragraphs), each expecting that story. Scored by
recall@10 on the real local model, so it is opt-in (the model download):
FFL_EMBED_EVAL=1 uv run pytest tests/test_news_search_eval.py -s"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ffl import db, embed
from ffl.newsdesk import embed as stage

pytestmark = [pytest.mark.real_embedder,
              pytest.mark.skipif(not os.environ.get("FFL_EMBED_EVAL"), reason="FFL_EMBED_EVAL=1 runs the real model")]

EVAL = json.loads((Path(__file__).parent / "fixtures" / "news_search_eval.json").read_text())
FLOOR = 0.8
DETAIL_FLOOR = 0.75


def test_recall_at_10():
    at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    ids = {}
    for s in EVAL["stories"]:
        sid = db.add_story({"first_item_id": None, "first_at": at, "updated_at": at, "kind": "injury"})
        db.put_story_players(sid, [{"player_id": 1, "effect": "down", "kind": "injury", "prob": 0.9, "confidence": 0.5}])
        db.put_story_words(sid, 1, s["headline"], s["overview"], {1: (s["line"], False)})
        ids[sid] = s["key"]
    by_key = {k: sid for sid, k in ids.items()}
    for n, it in enumerate(EVAL["items"]):
        [iid] = db.add_news_items([{"source_id": "eval", "ext_id": str(n), "url": "u", "published_at": at,
                                    "fetched_at": at, "title": it["title"], "text": it["text"], "hash": str(n)}])
        db.tag_item(iid, [1], None, by_key[it["key"]], at)
    stage.run()
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")

    def mean_recall(cases):
        recalls = []
        for case in cases:
            got = [ids[s["id"]] for s in db.search_stories(embed.query(case["q"]), case["q"], since)][:10]
            recalls.append(len(set(got) & set(case["expected"])) / len(case["expected"]))
            print(f"recall@10 {recalls[-1]:.2f}  {case['q']}")
        return sum(recalls) / len(recalls)

    situations, details = mean_recall(EVAL["queries"]), mean_recall(EVAL["detail_queries"])
    print(f"mean recall@10: situations {situations:.2f}, details {details:.2f}")
    assert situations >= FLOOR and details >= DETAIL_FLOOR
