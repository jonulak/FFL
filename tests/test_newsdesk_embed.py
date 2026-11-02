"""The embed stage and the story search behind GET /news/search, on conftest's fake embedder."""
from datetime import datetime, timedelta, timezone

from ffl import db, embed
from ffl.newsdesk import embed as stage
from ffl.newsdesk import pipeline
from tests.conftest import fake_vector

NOW = datetime.now(timezone.utc)


def at(days):
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def written(headline, overview="", days=1, lines=None):
    sid = db.add_story({"first_item_id": None, "first_at": at(days), "updated_at": at(days), "kind": "injury"})
    db.put_story_players(sid, [{"player_id": 1, "effect": "down", "kind": "injury", "prob": 0.9, "confidence": 0.5}])
    assert db.put_story_words(sid, 1, headline, overview, {1: (lines or "why", False)})
    return sid


def hits(q, days=30):
    return [s["id"] for s in db.search_stories(embed.query(q), q, at(days))]


def test_stage_embeds_written_stories_once_and_again_after_a_rewrite():
    a = written("Receiver limps off", "A hamstring.", lines="Game-time call for Sunday")
    db.add_story({"first_item_id": None, "first_at": at(1), "updated_at": at(1), "kind": "injury"})  # never written
    assert stage.run() == {"embedded": 1, "stale": 0}
    assert stage.run() == {"embedded": 0, "stale": 0}
    db.set_story_status(a, "corrected", at(0), None)
    assert db.put_story_words(a, 2, "Correction: receiver fine", "O", {1: ("r", False)})
    assert [s["id"] for s in db.unembedded_stories(10)] == [a]
    assert stage.run()["embedded"] == 1


def test_the_vector_of_a_story_rewritten_meanwhile_is_dropped():
    a = written("Old headline")
    [s] = db.unembedded_stories(10)
    db.ask_reword(a)
    assert db.put_story_words(a, 1, "New headline", "O", {1: ("r", False)}, reword=1)
    assert db.put_story_embedding(a, s["headline"], fake_vector("old")) is False
    assert db.unembedded_stories(10)[0]["headline"] == "New headline"


def test_a_model_failure_fails_only_the_embed_stage(monkeypatch):
    written("Anything")
    def boom(texts):
        raise RuntimeError("no model")
    monkeypatch.setattr(embed, "passages", boom)
    monkeypatch.setattr(pipeline.seed, "ensure", lambda: None)
    monkeypatch.setattr(pipeline.players, "fetch_index", lambda: None)  # the index stages skip
    monkeypatch.setattr(pipeline.collect, "recheck", lambda now: {})
    monkeypatch.setattr(pipeline, "_health", lambda: 0)
    out = pipeline.run()
    assert out["embed"] == {"error": "RuntimeError: no model"} and out["health"] == 0
    assert db.unembedded_stories(10)  # retried next run


def test_search_puts_headline_matches_first_then_meaning():
    near = written("Questionable for Sunday", "He is a game-time call.", days=2)
    far = written("Signs an extension", "Four years.", days=1)
    named = written("Puka Nacua extension talks", "Nothing close.", days=3)
    stage.run()
    assert hits("Puka")[0] == named
    assert hits("game-time call Sunday")[0] == near
    assert set(hits("game-time call Sunday")) == {near, far, named}  # top 10 of 3: every one, nearest first


def test_search_window_and_wildcards():
    old = written("100% healthy", days=40)
    new = written("100% healthy again", days=1)
    stage.run()
    assert hits("100%") == [new]  # the 30 days hold for both halves
    assert db.search_stories(None, "_", at(30)) == []  # a bare _ is a literal, not any character
    assert [s["id"] for s in db.search_stories(None, "healthy", at(60))] == [new, old]
    assert db.search_stories(None, "  ", at(60)) == []


def test_story_payloads_never_carry_the_vector():
    a = written("H")
    stage.run()
    assert "embedding" not in db.story(a) and "embedding" not in db.news_feed(None, 5)[0]
