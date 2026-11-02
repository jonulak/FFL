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
    assert stage.run().items() >= {"embedded": 1, "stale": 0}.items()
    assert stage.run().items() >= {"embedded": 0, "stale": 0}.items()
    db.set_story_status(a, "corrected", at(0), None)
    assert db.put_story_words(a, 2, "Correction: receiver fine", "O", {1: ("r", False)})
    assert [s["id"] for s in db.unembedded_stories()] == [a]
    assert stage.run()["embedded"] == 1


def test_the_vector_of_a_story_rewritten_meanwhile_is_dropped():
    a = written("Old headline")
    [s] = db.unembedded_stories()
    db.ask_reword(a)
    assert db.put_story_words(a, 1, "New headline", "O", {1: ("r", False)}, reword=1)
    assert db.put_story_embedding(a, s["headline"], fake_vector("old")) is False
    assert db.unembedded_stories()[0]["headline"] == "New headline"


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
    assert db.unembedded_stories()  # retried next run
    stages = list(out)
    assert stages.index("notices") < stages.index("embed") < stages.index("health")  # never delays a push


def test_one_run_backfills_past_a_chunk(monkeypatch):
    monkeypatch.setattr(stage, "BATCH", 2)
    for n in range(5):
        written(f"Story {n}")
    assert stage.run().items() >= {"embedded": 5, "stale": 0}.items()
    assert db.unembedded_stories() == []


def test_unembedded_rows_carry_only_the_words():
    a = written("H", "O", lines="the line")
    assert db.unembedded_stories() == [{"id": a, "headline": "H", "overview": "O", "reasons": ["the line"]}]


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


def item(sid, text, title="Practice report", days=1):
    [iid] = db.add_news_items([{"source_id": "beat", "ext_id": f"{sid}-{text[:30]}", "url": "u", "title": title,
                                "published_at": at(days), "fetched_at": at(days), "text": text, "hash": f"h-{text}"}])
    db.tag_item(iid, [1], None, sid, at(days))
    return iid


def test_chunks_keep_paragraphs_and_stay_under_max():
    assert stage.chunks("") == [] and stage.chunks("\n \n") == []
    assert stage.chunks("One line.\n\nTwo lines.") == ["One line.\nTwo lines."]
    out = stage.chunks("\n\n".join(f"Paragraph {n} " + "word " * 150 for n in range(6)))  # ~760 chars each
    assert len(out) == 3 and all(len(p) <= stage.MAX for p in out)
    assert [p.split(" ")[1] for p in out] == ["0", "2", "4"]  # long paragraphs: none carried over


def test_chunks_carry_a_short_last_paragraph_and_split_a_giant_one():
    x, y, z, short = "x" * 1000, "y" * 700, "z" * 1000, "Short tail."
    assert stage.chunks("\n".join([x, y, short, z])) == [f"{x}\n{y}\n{short}", f"{short}\n{z}"]
    giant = " ".join(f"Sentence number {n} is here." for n in range(300))  # ~8,000 chars in one paragraph
    parts = stage.chunks(giant)
    assert len(parts) >= 5 and all(len(p) <= stage.MAX and p.endswith(".") for p in parts)
    assert stage.chunks("a" * 4000) == ["a" * 1800, "a" * 1800, "a" * 400]  # no spaces: a hard cut


def test_stage_chunks_each_live_item_once_and_again_after_a_revision():
    sid = written("Receiver limps off")
    iid = item(sid, "He was limited Wednesday.\n\nCoach called him a game-time decision.")
    db.add_news_items([{"source_id": "beat", "ext_id": "loose", "url": "u", "published_at": at(1),
                        "fetched_at": at(1), "title": "t", "text": "never tagged", "hash": "h"}])  # on no story
    out = stage.run()
    assert (out["chunked_items"], out["chunks"]) == (1, 1)
    assert stage.run()["chunked_items"] == 0 and db.unchunked_items(10) == []
    db.update_item_text(iid, "Practice report", "Now ruled out.", "h2", at(0))
    assert [i["id"] for i in db.unchunked_items(10)] == [iid]
    assert db.put_item_chunks(iid, "h-stale", [("x", fake_vector("x"))]) is False  # revised since it was read
    assert stage.run()["chunked_items"] == 1
    [s] = db.search_stories(embed.query("ruled out"), "zzz", at(30))
    assert s["match"] == {"item_id": iid, "text": "Now ruled out."}  # only the latest text's pieces remain


def test_search_finds_a_detail_only_a_source_has():
    plain = written("Receiver limps off", "A hamstring.", lines="day to day")
    other = written("Kicker signs", "One year.", lines="depth")
    iid = item(plain, "Wednesday walkthrough notes.\n\nOkafor wore a red noncontact jersey at practice.")
    stage.run()
    first = db.search_stories(embed.query("red noncontact jersey"), "jersey", at(30))[0]
    assert first["id"] == plain and first["match"]["item_id"] == iid and "noncontact" in first["match"]["text"]
    got = [s["id"] for s in db.search_stories(embed.query("hamstring day to day"), "", at(30))]
    assert got.count(plain) == 1 and other in got  # best of its words and its pieces: once
    db.withdraw_item(iid, at(0))
    assert all(s["match"] is None for s in db.search_stories(embed.query("red noncontact jersey"), "", at(30)))


def test_a_story_outside_the_window_is_not_found_by_its_source():
    old = written("Old news", days=40)
    item(old, "a unique zebra detail", days=40)
    stage.run()
    assert old not in hits("unique zebra detail")
