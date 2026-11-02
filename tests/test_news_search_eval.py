"""The story search's eval: 100 written stories (20 situations x 5 fictional players), 5 queries worded unlike the
stories, each expecting its situation's 5. Scored by recall@10 on the real local model, so it is opt-in (the model
download): FFL_EMBED_EVAL=1 uv run pytest tests/test_news_search_eval.py -s"""
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


def test_recall_at_10():
    at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    ids = {}
    for s in EVAL["stories"]:
        sid = db.add_story({"first_item_id": None, "first_at": at, "updated_at": at, "kind": "injury"})
        db.put_story_players(sid, [{"player_id": 1, "effect": "down", "kind": "injury", "prob": 0.9, "confidence": 0.5}])
        db.put_story_words(sid, 1, s["headline"], s["overview"], {1: (s["line"], False)})
        ids[sid] = s["key"]
    while stage.run()["embedded"]:
        pass
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    recalls = []
    for case in EVAL["queries"]:
        got = [ids[s["id"]] for s in db.search_stories(embed.query(case["q"]), case["q"], since)][:10]
        recalls.append(len(set(got) & set(case["expected"])) / len(case["expected"]))
        print(f"recall@10 {recalls[-1]:.2f}  {case['q']}")
    mean = sum(recalls) / len(recalls)
    print(f"mean recall@10 {mean:.2f}")
    assert mean >= FLOOR
