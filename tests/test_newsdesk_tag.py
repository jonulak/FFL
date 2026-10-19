import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ffl import db
from ffl.newsdesk import jev, tag
from ffl.newsdesk.players import Index

FIX = Path(__file__).parent / "fixtures" / "newsdesk"
NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
AT = NOW.isoformat()
INDEX = Index(by_id={1: {"name": "Kyren Williams", "pos": "RB", "team": "LAR"},
                     2: {"name": "Puka Nacua", "pos": "WR", "team": "LAR"},
                     3: {"name": "Roschon Johnson", "pos": "RB", "team": "CHI"}},
              by_club={"LAR": [1, 2], "CHI": [3]},
              by_full={"kyren williams": [1], "puka nacua": [2], "roschon johnson": [3]})


def item(ext, title, text="", source="site:LAR", at="2026-10-19T12:00:00+00:00", club="LAR"):
    [i] = db.add_news_items([{"source_id": source, "ext_id": ext, "url": f"https://x/{ext}", "published_at": at,
                              "fetched_at": at, "title": title, "text": text, "club": club, "hash": f"h{ext}"}])
    return i


def story(players=(1,), effect="down", at="2026-10-19T10:00:00+00:00", first_item=None, kind="injury"):
    sid = db.add_story({"first_item_id": first_item, "first_at": at, "updated_at": at, "kind": "injury",
                        "headline": "Kyren Williams limited", "overview": ""})
    db.put_story_players(sid, [{"player_id": p, "effect": effect, "kind": kind, "prob": 0.6, "confidence": 0.5}
                               for p in players])
    return sid


def fake(story="new", retract=0.1, changed=0.2, about=None, effect="up", kind="injury", story_prob=0.8, fresh=0.9):
    """A Jev stand-in: every question answered; `story` is the choice for the story question."""
    calls = []

    def ask(state, qs):
        calls.append((state, qs))
        out = {}
        for k, q in qs.items():
            name, _, pid = k.partition(":")
            if q["type"] == "noul":
                v = (about or {}).get(int(pid), 0.95) if name == "about" else {"retract": retract, "changed": changed, "fresh": fresh}[name]
                out[k] = {"type": "noul", "noul": v}
                continue
            opts = list(q["criteria"])
            pick = {"story": story, "effect": effect, "kind": kind}[name]
            top = story_prob if name == "story" else 0.8
            probs = {o: top if o == pick else (1 - top) / (len(opts) - 1) for o in opts}
            out[k] = {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.7}
        return out

    ask.calls = calls
    return ask


def rows(sid):
    return {p["player_id"]: p for p in db.story(sid)["players"]}


def test_questions_shape():
    it = {"title": "Kyren Williams limited", "text": "body", "club": "LAR"}
    ps = [{"player_id": 1, **INDEX.by_id[1]}, {"player_id": 2, **INDEX.by_id[2]}]
    qs = tag.questions(it, ps, [])
    assert set(qs) == {f"{q}:{p}" for q in ("about", "fresh", "effect", "kind") for p in (1, 2)}
    assert qs["fresh:1"]["type"] == "noul" and "new information about Kyren Williams" in qs["fresh:1"]["instructions"]
    assert list(qs["effect:1"]["criteria"]) == ["up", "down", "none"]
    assert list(qs["kind:2"]["criteria"]) == ["injury", "return", "role", "depth", "other"]
    assert qs["about:1"]["type"] == "noul" and "Kyren Williams, the LAR RB" in qs["about:1"]["instructions"]
    assert "fantasy value over the coming weeks" in qs["effect:1"]["instructions"]
    cand = {"id": 7, "first_at": "2026-10-18T10:00:00+00:00", "updated_at": "2026-10-18T10:00:00+00:00",
            "headline": None, "items": [{"title": "Williams in walking boot"}], "names": ["Kyren Williams"]}
    qs = tag.questions(it, ps, [cand])
    assert qs["story"]["type"] == "choice" and qs["retract"]["type"] == "noul"
    assert qs["story"]["criteria"]["s7"] == "2026-10-18 Williams in walking boot — Kyren Williams"
    assert list(qs["story"]["criteria"])[0] == "new"


def test_story_options_capped():
    cands = [{"id": n, "first_at": "2026-10-17T00:00:00+00:00", "updated_at": f"2026-10-18T{n // 60:02d}:{n % 60:02d}:00+00:00",
              "headline": f"h{n}", "items": [], "names": []} for n in range(300)]
    qs = tag.questions({"title": "t", "text": "", "club": None}, [{"player_id": 1, **INDEX.by_id[1]}], cands)
    opts = list(qs["story"]["criteria"])
    assert len(opts) == 255 and opts[0] == "new"
    assert set(opts[1:]) == {f"s{n}" for n in range(46, 300)}


def test_read_takes_argmax_and_prob():
    spike = json.loads((FIX / "jev_answer.json").read_text())["b_roundup_20"]
    answers = spike["response"]["answers"]
    pids = [int(k.split(":")[1]) for k in spike["questions"] if k.startswith("effect:")]
    got = tag.read(answers, [{"player_id": p} for p in pids])
    by = {p["player_id"]: p for p in got["players"]}
    hurts = answers["effect:4040715"]
    assert by[4040715]["effect"] == hurts["choice"] == "down"
    assert by[4040715]["prob"] == hurts["probabilities"]["down"]
    assert by[4040715]["confidence"] == hurts["confidence"]
    assert by[4040715]["kind"] == "injury"
    assert 4240657 not in by  # safety Michael Carter II, not the RB: about 0.13
    assert len(by) == 19 and got["story"] is None  # the spike's story answer was "new"
    assert tag.read({**answers, "story": {"type": "choice", "choice": "s12", "probabilities": {}, "confidence": 1}},
                    [{"player_id": p} for p in pids])["story"] == 12


@pytest.mark.parametrize("fresh,effect", [(0.2, "none"), (0.65, "none"), (0.8, "down")])
def test_passing_mention_forced_none(fresh, effect):
    i = item("a", "Panthers preview", text="With Kyren Williams out, the Rams lean on the pass")
    tag.run(INDEX, ask=fake(effect="down", fresh=fresh), now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    row = rows(s["id"])[1]
    assert (row["effect"], row["kind"]) == (effect, "injury")
    assert row["prob"] == pytest.approx(0.1 if effect == "none" else 0.8)  # the shown effect's own probability
    assert s["items"][0]["jev"]["fresh:1"]["noul"] == fresh and s["items"][0]["id"] == i


def test_new_story_when_new():
    i = item("a", "Kyren Williams limited at practice")
    got = tag.run(INDEX, ask=fake(effect="down"), now=NOW)
    assert got["tagged"] == 1 and got["new"] == 1 and got["attached"] == 0
    [s] = db.recent_stories([1], "2026-10-01")
    [it] = s["items"]
    assert (it["id"], it["player_ids"], it["tagged_at"]) == (i, [1], AT)
    assert (s["first_item_id"], s["first_at"], s["updated_at"], s["rev"]) == (i, "2026-10-19T12:00:00+00:00", AT, 1)
    assert rows(s["id"])[1]["effect"] == "down" and rows(s["id"])[1]["prob"] == 0.8


def test_attach_when_story_chosen():
    sid = story()
    i = item("a", "Kyren Williams limited again", text="Puka Nacua full")
    ask = fake(story=f"s{sid}")
    got = tag.run(INDEX, ask=ask, now=NOW)
    assert got["new"] == 0 and got["attached"] == 1
    s = db.story(sid)
    assert [x["id"] for x in s["items"]] == [i]
    assert rows(sid)[1]["effect"] == "down"  # unchanged story: an existing player's row stands
    assert rows(sid)[2]["effect"] == "up"  # a player new to the story gets his row
    # and his arrow is news: the story is rewritten (his line) and may push
    assert (s["rev"], s["updated_at"], got["updated"]) == (2, AT, 1)
    assert "story" in ask.calls[0][1] and "retract" in ask.calls[0][1]


def test_new_player_without_an_arrow_does_not_bump():
    sid = story()
    item("a", "Kyren Williams limited again", text="Puka Nacua full")
    got = tag.run(INDEX, ask=fake(story=f"s{sid}", effect="none"), now=NOW)
    s = db.story(sid)
    assert got["attached"] == 1 and got["updated"] == 0
    assert (s["rev"], s["updated_at"]) == (1, "2026-10-19T10:00:00+00:00") and rows(sid)[2]["effect"] == "none"


def test_attach_needs_same_kind():
    recap = story(kind="other")
    item("a", "Kyren Williams limited with an ankle injury")
    got = tag.run(INDEX, ask=fake(story=f"s{recap}", kind="injury"), now=NOW)
    assert got["new"] == 1 and got["attached"] == 0 and db.story(recap)["items"] == []


def test_attach_needs_prob():
    sid = story()
    item("a", "Kyren Williams limited again")
    got = tag.run(INDEX, ask=fake(story=f"s{sid}", story_prob=0.55), now=NOW)
    assert got["new"] == 1 and got["attached"] == 0 and db.story(sid)["items"] == []
    item("b", "Kyren Williams limited on Friday")
    got = tag.run(INDEX, ask=fake(story=f"s{sid}", story_prob=0.6), now=NOW)
    assert got["attached"] == 1


@pytest.mark.parametrize("changed,rev", [(0.7, 2), (0.3, 1)])
def test_changed_bumps_rev(changed, rev):
    sid = story()
    item("a", "Kyren Williams back at full practice")
    got = tag.run(INDEX, ask=fake(story=f"s{sid}", changed=changed), now=NOW)
    s = db.story(sid)
    assert s["rev"] == rev and got["updated"] == (rev - 1)
    if rev == 2:
        assert s["updated_at"] == AT and rows(sid)[1]["effect"] == "up"
    else:
        assert s["updated_at"] == "2026-10-19T10:00:00+00:00" and rows(sid)[1]["effect"] == "down"


def test_earlier_item_becomes_first():
    first = item("first", "Kyren Williams hurt", at="2026-10-19T10:00:00+00:00")
    sid = story(first_item=first)
    db.tag_item(first, [1], None, sid, AT)
    earlier = item("b", "Kyren Williams leaves practice", at="2026-10-19T08:00:00+00:00")
    tag.run(INDEX, ask=fake(story=f"s{sid}"), now=NOW)
    s = db.story(sid)
    assert (s["first_item_id"], s["first_at"]) == (earlier, "2026-10-19T08:00:00+00:00")


def test_correction_marks_story_corrected():
    sid = story()
    i = item("a", "Kyren Williams report was wrong", source="bsky:beat")
    got = tag.run(INDEX, ask=fake(story=f"s{sid}", retract=0.8, changed=0.9), now=NOW)
    s = db.story(sid)
    assert (s["status"], s["rev"], s["status_note"], s["status_at"]) == ("corrected", 2, "bsky:beat", AT)
    assert [x["id"] for x in s["items"]] == [i] and got["updated"] == 1
    assert rows(sid)[1]["effect"] == "up"  # the correction is the current fact


def test_retract_without_a_story_is_a_new_story():
    sid = story()
    item("a", "Kyren Williams report was wrong")
    got = tag.run(INDEX, ask=fake(story="new", retract=0.8), now=NOW)
    assert got["new"] == 1 and db.story(sid)["status"] == "live"


def test_edited_item_retagged_on_own_story():
    sid, other = story(), story()
    i = item("a", "Kyren Williams limited")
    db.tag_item(i, [1], None, sid, "2026-10-19T12:30:00+00:00")
    db.update_item_text(i, "Kyren Williams ruled out", "", "h2", "2026-10-19T14:00:00+00:00")
    ask = fake(story=f"s{other}", changed=0.7)
    got = tag.run(INDEX, ask=ask, now=NOW)
    assert all("story" not in qs for _, qs in ask.calls)
    assert db.story(sid)["rev"] == 2 and db.story(other)["rev"] == 1
    assert [x["id"] for x in db.story(sid)["items"]] == [i] and rows(sid)[1]["effect"] == "up"
    assert got["updated"] == 1 and got["new"] == 0


@pytest.mark.parametrize("others", [False, True])
def test_edited_item_about_nobody_now_leaves_its_story(others):
    sid = story()
    i = item("a", "Kyren Williams limited")
    db.tag_item(i, [1], None, sid, "2026-10-19T12:30:00+00:00")
    if others:
        db.tag_item(item("b", "Kyren Williams limited again"), [1], None, sid, "2026-10-19T12:30:00+00:00")
    db.update_item_text(i, "Kyren Williams the coach", "", "h2", "2026-10-19T14:00:00+00:00")
    got = tag.run(INDEX, ask=fake(about={1: 0.1}), now=NOW)
    s = db.story(sid)
    assert got["tagged"] == 1 and db.untagged_items() == []
    assert [x["withdrawn_at"] for x in s["items"] if x["id"] == i] == [AT]  # no longer one of its sources
    # with no live source left the story is retracted (and re-surfaces), as a deleted source would leave it
    assert (s["status"], s["rev"]) == (("live", 1) if others else ("retracted", 2))


def test_item_without_players_has_no_story():
    i = item("a", "Rams sign a long snapper")
    ask = fake()
    got = tag.run(INDEX, ask=ask, now=NOW)
    assert ask.calls == [] and got["tagged"] == 1 and got["new"] == 0
    assert db.untagged_items() == []
    with db._read() as c:
        r = c.execute(db.sa.select(db.news_items_table).where(db.news_items_table.c.id == i)).first()
    assert (r.story_id, r.tagged_at, r.player_ids) == (None, AT, [])


def test_not_about_player_dropped():
    i = item("a", "Kyren Williams and Puka Nacua", text="Coach said Puka Nacua the punter, not the receiver")
    tag.run(INDEX, ask=fake(about={2: 0.08}), now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    assert set(rows(s["id"])) == {1}
    assert [x["player_ids"] for x in s["items"] if x["id"] == i] == [[1]]


def test_all_players_not_about_leaves_no_story():
    sid = story(players=(3,))
    item("a", "Ben Johnson proud of the line", text="Johnson said the line was great", source="bsky:x", club="CHI")
    got = tag.run(INDEX, ask=fake(story=f"s{sid}", about={3: 0.08}), now=NOW)
    assert got["tagged"] == 1 and got["new"] == 0 and got["attached"] == 0
    assert db.story(sid)["items"] == []
    assert db.untagged_items() == []


def test_media_item_player_from_ext_id():
    i = item("vid#2", "Sean McVay press conference", text="he had a great day, and Kyren Williams ran hard",
             source="yt:LAR")
    ask = fake()
    tag.run(INDEX, ask=ask, now=NOW)
    assert {k for k in ask.calls[0][1] if k.startswith("about:")} == {"about:2", "about:1"}
    [s] = db.recent_stories([2], "2026-10-01")
    assert set(rows(s["id"])) == {1, 2} and s["items"][0]["id"] == i


def test_bad_item_skipped_outage_stops_run():
    a = item("a", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    item("b", "Puka Nacua limited", at="2026-10-19T11:00:00+00:00")
    good = fake()

    def ask(state, qs):
        if "Kyren" in state["title"]:
            raise jev.JevError("too long", 400)
        return good(state, qs)

    got = tag.run(INDEX, ask=ask, now=NOW)
    assert got["failed"] == [a] and got["tagged"] == 1 and got["error"] is None
    assert [u["id"] for u in db.untagged_items()] == [a]

    def down(state, qs):
        raise jev.JevError("jev 529", 529)

    item("c", "Puka Nacua questionable", at="2026-10-19T12:00:00+00:00")
    got = tag.run(INDEX, ask=down, now=NOW)
    assert got["tagged"] == 0 and "529" in got["error"]
    assert len(db.untagged_items()) == 2


def flaky(bad, error):
    """Jev answering every item but the ones titled with `bad`, where `error` is raised (or returned, a dict)."""
    good = fake()

    def ask(state, qs):
        if bad in state["title"]:
            if isinstance(error, dict):
                return error
            raise error
        return good(state, qs)
    return ask


@pytest.mark.parametrize("error", [{}, {"about:1": {"noul": 0.9}}, jev.JevError("jev: no answer to ['kind:1']", 200),
                                   jev.JevError("jev 400: nope", 400)])
def test_one_bad_answer_skips_the_item_not_the_run(error):
    a = item("a", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    item("b", "Puka Nacua limited", at="2026-10-19T11:00:00+00:00")
    got = tag.run(INDEX, ask=flaky("Kyren", error), now=NOW)
    assert got["failed"] == [a] and got["tagged"] == 1 and got["error"] is None
    assert [u["id"] for u in db.untagged_items()] == [a]
    assert db.get_state(tag.TRIES) == {str(a): 1}


@pytest.mark.parametrize("error", [jev.JevError("jev 401: bad key", 401), jev.JevError("jev 402: no credits", 402),
                                   jev.JevError("jev 403: revoked", 403), jev.JevError("jev 429: slow down", 429),
                                   jev.JevError("no TYPESAFE_API_KEY in .env", account=True)])
def test_account_failure_stops_the_run_and_counts_nothing(error):
    """A dead key or no credits fails every item alike: the run stops and no item moves towards giving up, even
    after Jev has answered earlier items this run."""
    item("a", "Puka Nacua limited", at="2026-10-19T12:00:00+00:00")  # newest: tagged first, so Jev has answered
    b = item("b", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    c = item("c", "Kyren Williams questionable", at="2026-10-19T11:00:00+00:00")
    for _ in range(tag.TAG_MAX_TRIES + 1):
        got = tag.run(INDEX, ask=flaky("Kyren", error), now=NOW)
        assert got["error"] == str(error) and got["failed"] == [] and got["given_up"] == []
    assert sorted(u["id"] for u in db.untagged_items()) == [b, c]
    assert not db.get_state(tag.TRIES)


def test_untagged_newest_first():
    a = item("a", "Puka Nacua limited", at="2026-10-19T09:00:00+00:00")
    b = item("b", "Kyren Williams limited", at="2026-10-19T11:00:00+00:00")
    assert [u["id"] for u in db.untagged_items()] == [b, a]


def test_older_item_joining_newer_news_never_changes_or_corrects_it():
    """A backlog is tagged newest first: Saturday's "questionable" joining Monday's "out" story is a source of it,
    not a change to it, so Jev's changed/retract answers are not acted on and the story keeps its rev."""
    sid = story()
    new = item("new", "Kyren Williams out", at="2026-10-19T11:00:00+00:00")
    db.tag_item(new, [1], None, sid, NOW.isoformat())
    old = item("old", "Kyren Williams questionable", at="2026-10-17T11:00:00+00:00")
    ask = fake(story=f"s{sid}", retract=0.9, changed=0.9)
    got = tag.run(INDEX, ask=ask, now=NOW)
    assert got["attached"] == 1 and got["updated"] == 0
    s = db.story(sid)
    assert s["rev"] == 1 and s["status"] == "live" and s["first_item_id"] == old
    assert not any("changed" in qs for _, qs in ask.calls)


def test_server_error_after_an_answer_is_the_items():
    item("a", "Puka Nacua limited", at="2026-10-19T09:00:00+00:00")
    b = item("b", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    item("c", "Puka Nacua questionable", at="2026-10-19T11:00:00+00:00")
    got = tag.run(INDEX, ask=flaky("Kyren", jev.JevError("jev 500", 500)), now=NOW)
    assert got["failed"] == [b] and got["tagged"] == 2 and got["error"] is None


def test_server_error_first_is_the_items_when_the_next_answers():
    """The item heading the queue may be the one Jev chokes on: it must not stop tagging for good."""
    a = item("a", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    item("b", "Puka Nacua limited", at="2026-10-19T11:00:00+00:00")
    got = tag.run(INDEX, ask=flaky("Kyren", jev.JevError("jev 502", 502)), now=NOW)
    assert got["failed"] == [a] and got["tagged"] == 1 and got["error"] is None
    assert db.get_state(tag.TRIES) == {str(a): 1}


@pytest.mark.parametrize("error", [jev.JevError("jev: connection refused"), jev.JevError("jev 503", 503)])
def test_outage_stops_the_run_and_counts_nothing(error):
    item("a", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    item("b", "Puka Nacua limited", at="2026-10-19T11:00:00+00:00")
    item("c", "Puka Nacua questionable", at="2026-10-19T12:00:00+00:00")
    calls = []

    def down(state, qs):
        calls.append(state["title"])
        raise error

    got = tag.run(INDEX, ask=down, now=NOW)
    assert got["tagged"] == 0 and str(error) in got["error"] and len(calls) == 2
    assert len(db.untagged_items()) == 3 and not db.get_state(tag.TRIES)


def test_item_given_up_after_max_tries():
    a = item("a", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    ask = flaky("Kyren", {})
    for n in range(1, tag.TAG_MAX_TRIES):
        assert tag.run(INDEX, ask=ask, now=NOW)["failed"] == [a]
        assert db.get_state(tag.TRIES) == {str(a): n}
    got = tag.run(INDEX, ask=ask, now=NOW)
    assert got["failed"] == [a] and got["given_up"] == [a]
    assert db.untagged_items() == [] and db.get_state(tag.TRIES) == {}
    with db._read() as c:
        r = c.execute(db.sa.select(db.news_items_table).where(db.news_items_table.c.id == a)).first()
    assert (r.story_id, r.tagged_at, r.player_ids) == (None, AT, [])
