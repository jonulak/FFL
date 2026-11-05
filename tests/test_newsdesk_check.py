import pytest

from ffl import db
from ffl.newsdesk import check
from ffl.newsdesk.players import Index

AT = "2026-10-19T12:00:00+00:00"
INDEX = Index(by_id={1: {"name": "Kyren Williams", "pos": "RB", "team": "LAR"}, 2: {"name": "Puka Nacua", "pos": "WR", "team": "LAR"}})


def make(players=((1, "down", 0.9),), title="Kyren Williams ruled out", n="a"):
    sid = db.add_story({"first_item_id": None, "first_at": AT, "updated_at": AT, "kind": "injury"})
    [i] = db.add_news_items([{"source_id": "pft", "ext_id": f"{n}{sid}", "url": "u", "published_at": AT, "fetched_at": AT,
                              "title": title, "text": "Body.", "club": "LAR", "hash": f"h{n}{sid}"}])
    db.tag_item(i, [p for p, _, _ in players], None, sid, AT)
    db.put_story_players(sid, [{"player_id": p, "effect": e, "kind": "injury", "prob": pr, "confidence": pr} for p, e, pr in players])
    db.put_story_words(sid, 1, "H", "O", {p: ("Line.", False) for p, _, _ in players})
    return sid


def flag(sid, pid=1):
    return next(p["flag"] for p in db.story(sid)["players"] if p["player_id"] == pid)


def says(*answers):
    """A Haiku stand-in; answers are (about_him, effect), in the order asked."""
    calls = []

    def run(prompt, **kw):
        calls.append((prompt, kw))
        return {"answers": [{"n": n, "about_him": a, "effect": e} for n, (a, e) in enumerate(answers)]}
    run.calls = calls
    return run


@pytest.mark.parametrize("answer,flagged", [((True, "down"), False), ((True, "up"), True), ((True, "none"), True), ((False, "down"), True)])
def test_the_arrow_stands_only_if_haiku_reaches_it_alone(answer, flagged):
    sid = make()
    got = check.run([(sid, 1)], INDEX, run_cli=says(answer))
    assert got["checked_ids"] == [(sid, 1)] and got["error"] is None and flag(sid) is flagged
    assert got["flagged"] == int(flagged) and db.get_state(check.QUEUE) == []


def test_haiku_is_never_told_the_arrow():
    sid = make()
    run = says((True, "down"))
    check.run([(sid, 1)], INDEX, run_cli=run)
    prompt, kw = run.calls[0]
    assert "Kyren Williams, RB, LAR" in prompt and '<source' in prompt and "Kyren Williams ruled out" in prompt
    after = prompt.split("## Report", 1)[1]
    assert "effect" not in after and "down" not in after
    assert kw["model"] == check.MODEL and kw["kind"] == "news_check"


def test_a_failed_call_holds_the_push_for_the_next_run():
    sid = make()

    def down(prompt, **kw):
        raise RuntimeError("claude exited 1")

    got = check.run([(sid, 1)], INDEX, run_cli=down)
    assert got["checked_ids"] == [] and "exited 1" in got["error"] and got["waiting"] == 1
    assert [p[:2] for p in db.get_state(check.QUEUE)] == [[sid, 1]]
    got = check.run([], INDEX, run_cli=says((True, "down")))  # the next run, nothing new written
    assert got["checked_ids"] == [(sid, 1)] and db.get_state(check.QUEUE) == []


def test_a_missing_answer_holds_that_revision():
    sid = make()
    got = check.run([(sid, 1)], INDEX, run_cli=lambda p, **k: {"answers": []})
    assert got["checked_ids"] == [] and got["waiting"] == 1


def test_a_story_with_nothing_that_could_push_needs_no_call():
    none, weak = make(((1, "none", 1.0),), n="n"), make(((1, "up", 0.6),), n="w")
    run = says()
    got = check.run([(none, 1), (weak, 1)], INDEX, run_cli=run)
    assert run.calls == [] and sorted(got["checked_ids"]) == sorted([(none, 1), (weak, 1)])


def test_a_story_that_moved_on_is_dropped():
    sid = make()
    with db._write() as c:
        db._bump_story(c, sid, db.SEASON)
    run = says()
    got = check.run([(sid, 1)], INDEX, run_cli=run)
    assert run.calls == [] and got["checked_ids"] == [] and db.get_state(check.QUEUE) == []


def test_every_arrow_of_a_story_must_be_answered_and_batches_are_capped():
    sids = [make(n=f"b{i}") for i in range(check.BATCH + 1)]

    def run(prompt, **kw):
        return {"answers": [{"n": n, "about_him": True, "effect": "down"} for n in range(prompt.count("## Report"))]}

    got = check.run([(s, 1) for s in sids], INDEX, run_cli=run)
    assert sorted(got["checked_ids"]) == sorted((s, 1) for s in sids) and got["usage"]["calls"] == 2


def test_report_is_the_newest_source_naming_him():
    s = {"items": [{"id": 1, "published_at": "2026-10-19T01:00:00+00:00", "player_ids": [1], "title": "Mine"},
                   {"id": 2, "published_at": "2026-10-19T02:00:00+00:00", "player_ids": [2], "title": "Other"}]}
    assert check.report(s, 1)["title"] == "Mine" and check.report(s, 9)["title"] == "Other"
