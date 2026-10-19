import pytest

from ffl import db
from ffl.newsdesk import write
from ffl.newsdesk.players import Index

INDEX = Index(by_id={1: {"name": "Kyren Williams", "pos": "RB", "team": "LAR"},
                     2: {"name": "Puka Nacua", "pos": "WR", "team": "LAR"}})
AT = "2026-10-19T12:00:00+00:00"


def make(players=((1, "up"), (2, "none")), n_items=2, jev=None):
    sid = db.add_story({"first_item_id": None, "first_at": AT, "updated_at": AT, "kind": "injury",
                        "headline": "", "overview": ""})
    db.put_story_players(sid, [{"player_id": p, "effect": e, "kind": "injury", "prob": 0.7, "confidence": 0.5}
                               for p, e in players])
    ids = db.add_news_items([{"source_id": f"s{k}", "ext_id": f"{sid}-{k}", "url": f"https://x/{k}", "published_at": f"{AT[:-6]}{k}+00:00",
                              "fetched_at": AT, "title": f"Title {sid}-{k}", "text": f"Body text {sid}-{k}", "club": "LAR",
                              "hash": f"h{sid}{k}"} for k in range(n_items)])
    for i in ids:
        db.tag_item(i, [p for p, _ in players], jev, sid, AT)
    return sid


def answer(sid, disagree=False):
    return {"stories": [{"id": sid, "headline": "Williams limited", "overview": "He is hurt.",
                         "players": [{"player_id": 1, "reason": "Out with a knee.", "disagree": disagree},
                                     {"player_id": 2, "reason": "Only named.", "disagree": False}]}]}


def test_prompt_carries_jev_effects():
    sid = make()
    text = write.prompt(db.unwritten_stories(10), INDEX)
    assert "Kyren Williams" in text and "up" in text and "Puka Nacua" in text and "none" in text
    assert "Body text %d-0" % sid in text
    assert "Title %d-1" % sid in text and "Body text %d-1" % sid not in text


def test_prompt_says_corrected_and_passing_mention():
    sid = make(jev={"fresh:2": {"type": "noul", "noul": 0.55}})
    db.set_story_status(sid, "corrected", AT, "PFT")
    text = write.prompt(db.unwritten_stories(10), INDEX)
    assert "status: corrected; note: PFT" in text and "passing mention" in text
    assert text.count("the model judged this a passing mention") == 1  # Nacua only; Williams is up


def test_run_writes_and_flags():
    sid = make()
    calls = []

    def fake(prompt, **kw):
        calls.append(kw)
        return answer(sid, disagree=True)

    out = write.run(INDEX, run_cli=fake)
    assert calls[0]["model"] == "haiku" and calls[0]["tools"] == "" and calls[0]["timeout"] == 180
    assert out == {"written": 1, "written_ids": [(sid, 1)], "missing": 0, "failed": False, "error": None}
    s = db.story(sid)
    assert (s["headline"], s["written_rev"]) == ("Williams limited", 1)
    rows = {p["player_id"]: p for p in s["players"]}
    assert (rows[1]["reason"], rows[1]["flag"]) == ("Out with a knee.", True)
    assert rows[2]["flag"] is False


def test_missing_story_stays_unwritten():
    a, b = make(), make()
    out = write.run(INDEX, run_cli=lambda prompt, **kw: answer(a))
    assert (out["written"], out["missing"], out["written_ids"]) == (1, 1, [(a, 1)])
    assert [s["id"] for s in db.unwritten_stories(10)] == [b]


def test_failure_stops_and_reports(monkeypatch):
    monkeypatch.setattr(write, "BATCH", 1)
    make(), make()
    calls = []

    def boom(prompt, **kw):
        calls.append(1)
        raise RuntimeError("claude: usage limit reached")

    out = write.run(INDEX, run_cli=boom)
    assert len(calls) == 1
    assert (out["written"], out["failed"], out["missing"]) == (0, True, 2)
    assert "usage limit" in out["error"]
    assert len(db.unwritten_stories(10)) == 2


def test_stale_rev_not_written():
    sid = make()

    def fake(prompt, **kw):
        db.set_story_status(sid, "corrected", AT, "PFT")  # the story moves on while Haiku writes
        return answer(sid)

    out = write.run(INDEX, run_cli=fake)
    assert (out["written"], out["missing"], out["written_ids"]) == (0, 1, [])
    assert db.story(sid)["written_rev"] == 0
