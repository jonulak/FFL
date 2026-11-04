import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ffl import db
from ffl.newsdesk import tag
from ffl.newsdesk.players import Index

FIX = Path(__file__).parent / "fixtures" / "newsdesk"
NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)
AT = NOW.isoformat()
OLD = "2026-10-17T10:00:00+00:00"  # inside tag.WINDOW, past tag.THREAD_HOURS
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


def fake(story="new", retract=0.1, about=None, effect="up", kind="injury", story_prob=0.8, fresh=0.9, subject=None):
    """A rules stand-in: every player answered; `story` is the choice for the story question when there are candidates."""
    calls = []

    def choice(pick, top, opts):
        return {"type": "choice", "choice": pick, "confidence": 0.7,
                "probabilities": {o: top if o == pick else (1 - top) / (len(opts) - 1) for o in opts}}

    def answer(item, players, cands, index=None, now=None):
        calls.append((item, players, cands))
        out = {}
        for p in players:
            pid = p["player_id"]
            out[f"about:{pid}"] = {"type": "noul", "noul": (about or {}).get(pid, 0.95)}
            out[f"subject:{pid}"] = {"type": "noul", "noul": (subject or {}).get(pid, 0.9)}
            out[f"fresh:{pid}"] = {"type": "noul", "noul": fresh}
            out[f"effect:{pid}"] = choice(effect, 0.8, ["up", "down", "none"])
            out[f"kind:{pid}"] = choice(kind, 0.8, ["injury", "return", "role", "depth", "other"])
        if cands:
            out["story"] = choice(story, story_prob, ["new", story] if story != "new" else ["new", "x"])
            out["retract"] = {"type": "noul", "noul": retract}
        return out

    answer.calls = calls
    return answer


def rows(sid):
    return {p["player_id"]: p for p in db.story(sid)["players"]}


def test_read_takes_argmax_and_prob():
    spike = json.loads((FIX / "tag_answer.json").read_text())["b_roundup_20"]
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
    tag.run(INDEX, answer=fake(effect="down", fresh=fresh), now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    row = rows(s["id"])[1]
    assert (row["effect"], row["kind"]) == (effect, "injury")
    assert row["prob"] == pytest.approx(0.1 if effect == "none" else 0.8)  # the shown effect's own probability
    assert s["items"][0]["jev"]["fresh:1"]["noul"] == fresh and s["items"][0]["id"] == i



def _ans(subject=None):
    out = {}
    for pid in (1, 2):
        out[f"about:{pid}"] = {"type": "noul", "noul": 0.95}
        out[f"effect:{pid}"] = {"type": "choice", "choice": "down", "probabilities": {"down": 0.8}, "confidence": 0.7}
        out[f"kind:{pid}"] = {"type": "choice", "choice": "injury", "probabilities": {"injury": 0.9}, "confidence": 0.7}
        if subject is not None:
            out[f"subject:{pid}"] = {"type": "noul", "noul": subject[pid]}
    return out


def test_read_drops_a_passing_mention():
    ps = [{"player_id": 1}, {"player_id": 2}]
    got = tag.read(_ans({1: 0.9, 2: tag.SUBJECT - 0.01}), ps)
    assert [p["player_id"] for p in got["players"]] == [1]
    assert [p["player_id"] for p in tag.read(_ans({1: tag.SUBJECT, 2: 0.2}), ps)["players"]] == [1]


def test_read_old_answers_without_subject_keep_him():
    assert [p["player_id"] for p in tag.read(_ans(), [{"player_id": 1}, {"player_id": 2}])["players"]] == [1, 2]


def test_passing_mention_left_off_the_story():
    item("a", "Week 8 waiver roundup", text="Kyren Williams limited; Puka Nacua also on the list")
    tag.run(INDEX, answer=fake(effect="down", subject={2: 0.2}), now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    assert set(rows(s["id"])) == {1} and s["items"][0]["player_ids"] == [1]
    assert db.recent_stories([2], "2026-10-01") == []

def test_new_story_when_new():
    i = item("a", "Kyren Williams limited at practice")
    got = tag.run(INDEX, answer=fake(effect="down"), now=NOW)
    assert got["tagged"] == 1 and got["new"] == 1 and got["attached"] == 0
    [s] = db.recent_stories([1], "2026-10-01")
    [it] = s["items"]
    assert (it["id"], it["player_ids"], it["tagged_at"]) == (i, [1], AT)
    assert (s["first_item_id"], s["first_at"], s["updated_at"], s["rev"]) == (i, "2026-10-19T12:00:00+00:00", AT, 1)
    assert rows(s["id"])[1]["effect"] == "down" and rows(s["id"])[1]["prob"] == 0.8


def test_attach_when_story_chosen():
    sid = story()
    i = item("a", "Kyren Williams limited again", text="Puka Nacua full")
    base = fake(story=f"s{sid}")

    def ask(*a, **k):
        out = base(*a, **k)
        out["effect:1"] = {"type": "choice", "choice": "none", "probabilities": {"none": 0.9}, "confidence": 0.9}
        return out

    got = tag.run(INDEX, answer=ask, now=NOW)
    assert got["new"] == 0 and got["attached"] == 1
    s = db.story(sid)
    assert [x["id"] for x in s["items"]] == [i]
    assert rows(sid)[1]["effect"] == "down"  # no step for him: his row stands
    assert rows(sid)[2]["effect"] == "up"  # a player new to the story gets his row
    # and his arrow is news: the story is rewritten (his line) and may push
    assert (s["rev"], s["updated_at"], got["updated"]) == (2, AT, 1)


def test_a_roundup_moving_two_players_is_not_picked_into_ones_story():
    sid = story(players=(1,), at=OLD)  # past THREAD_HOURS: only the pick could join it
    item("a", "Kyren Williams and Puka Nacua ruled out")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", effect="down"), now=NOW)
    assert got["new"] == 1 and db.story(sid)["items"] == [] and set(rows(sid)) == {1}


def test_a_player_with_his_own_story_keeps_his_arrow_there():
    mine, his = story(players=(1,)), story(players=(2,), kind="injury")
    base = fake(story=f"s{mine}", effect="none")

    def ask(*a, **k):
        out = base(*a, **k)
        out["effect:2"] = {"type": "choice", "choice": "down", "probabilities": {"down": 0.9}, "confidence": 0.9}
        return out

    item("a", "Kyren Williams and Puka Nacua on the report")
    tag.run(INDEX, answer=ask, now=NOW)
    assert 2 not in rows(mine) and set(rows(his)) == {2}


def test_new_player_without_an_arrow_does_not_bump():
    sid = story()
    item("a", "Kyren Williams limited again", text="Puka Nacua full")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", effect="none"), now=NOW)
    s = db.story(sid)
    assert got["attached"] == 1 and got["updated"] == 0
    assert (s["rev"], s["updated_at"]) == (1, "2026-10-19T10:00:00+00:00") and rows(sid)[2]["effect"] == "none"


def test_attach_needs_same_kind():
    recap = story(kind="other")
    item("a", "Kyren Williams limited with an ankle injury")
    got = tag.run(INDEX, answer=fake(story=f"s{recap}", kind="injury"), now=NOW)
    assert got["new"] == 1 and got["attached"] == 0 and db.story(recap)["items"] == []


def test_role_and_depth_are_one_kind_of_news():
    """A signing is called role by one source and depth by the next (Dalton to Atlanta, 2026-10-19: five stories).
    Both count as the same news for joining, through the tagger's pick and through his thread."""
    picked = story(kind="role", at=OLD)  # past THREAD_HOURS: only the tagger's pick can join it
    item("a", "Kyren Williams moves up the depth chart")
    got = tag.run(INDEX, answer=fake(story=f"s{picked}", kind="depth"), now=NOW)
    assert got["attached"] == 1 and len(db.story(picked)["items"]) == 1
    threaded = story(players=(2,), kind="depth", effect="up")
    item("b", "Puka Nacua's role grows")
    got = tag.run(INDEX, answer=fake(story="new", kind="role", effect="up"), now=NOW)
    assert got["attached"] == 1 and got["new"] == 0 and len(db.story(threaded)["items"]) == 1


def test_attach_needs_prob():
    sid = story(at=OLD)  # a candidate, but past THREAD_HOURS: only the tagger's pick can join it
    item("a", "Kyren Williams limited again")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", story_prob=0.55), now=NOW)
    assert got["new"] == 1 and got["attached"] == 0 and db.story(sid)["items"] == []
    item("b", "Kyren Williams limited on Friday")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", story_prob=0.6), now=NOW)
    assert got["attached"] == 1


def test_status_steps_thread_into_one_story():
    """Doubtful, then out, then IR: the tagger calls each step a new development, but they are one developing situation,
    each step an update to it."""
    for n, (ext, title) in enumerate([("a", "Kyren Williams doubtful"), ("b", "Kyren Williams ruled out"),
                                      ("c", "Rams place Kyren Williams on IR")]):
        item(ext, title, at=f"2026-10-19T1{n}:00:00+00:00")
        tag.run(INDEX, answer=fake(story="new", effect="down"), now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    assert s["rev"] == 3 and len(s["items"]) == 3


def test_roundup_adds_no_rows_to_anothers_thread():
    sid = story(players=(1,))
    item("a", "Rams injury report", text="Kyren Williams ruled out; Puka Nacua limited")
    got = tag.run(INDEX, answer=fake(story="new", effect="down"), now=NOW)
    assert got["attached"] == 1 and got["new"] == 0
    assert set(rows(sid)) == {1}  # Nacua's news is not on Williams's story
    assert db.story(sid)["rev"] == 2


def test_thread_joins_only_the_story_he_leads():
    """A roundup naming two hurt players founds a story led by the surer one (here a tie: the first named). The
    other's next news is a story of his own, and only the lead's threads into the roundup's."""
    item("r", "Rams injuries", text="Kyren Williams is out; Puka Nacua is out", at="2026-10-19T10:00:00+00:00")
    tag.run(INDEX, answer=fake(story="new", effect="down"), now=NOW)
    [roundup] = db.recent_stories([1], "2026-10-01")
    assert tag._lead(roundup) == 1
    item("n", "Puka Nacua to IR", at="2026-10-19T11:00:00+00:00")
    item("k", "Kyren Williams to IR", at="2026-10-19T11:00:00+00:00")
    got = tag.run(INDEX, answer=fake(story="new", effect="down"), now=NOW)
    assert got["new"] == 1 and got["attached"] == 1
    assert [i["title"] for i in db.story(roundup["id"])["items"]] == ["Rams injuries", "Kyren Williams to IR"]


def test_a_passing_mention_never_erases_an_arrow():
    """A recap moves nobody, so it does not bump the story, and a ● for him does not overwrite his ▼: the story stays
    his, so the next step of his situation can still thread into it."""
    sid = story(players=(1,))
    item("a", "Rams fall to Cardinals", text="Kyren Williams watched; Puka Nacua had 100 yards")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", effect="none"), now=NOW)
    assert got["updated"] == 0 and db.story(sid)["rev"] == 1
    assert rows(sid)[1]["effect"] == "down" and rows(sid)[2]["effect"] == "none"


def test_thread_takes_the_surest_player():
    williams, nacua = story(players=(1,)), story(players=(2,))
    item("a", "Rams injury report", text="Kyren Williams ruled out; Puka Nacua limited")
    base = fake(story="new", effect="down")

    def ask(*a, **k):
        out = base(*a, **k)
        if "effect:2" in out:
            out["effect:2"]["probabilities"]["down"] = 0.95
        return out

    tag.run(INDEX, answer=ask, now=NOW)
    assert len(db.story(nacua)["items"]) == 1 and db.story(williams)["items"] == []


@pytest.mark.parametrize("row,kind,fresh,at", [("none", "injury", 0.9, AT),  # his row on it is ●
                                               ("down", "other", 0.9, AT),  # another kind of news about him
                                               ("down", "injury", 0.5, AT),  # a passing mention: ● on this item
                                               ("down", "injury", 0.9, OLD)])  # past THREAD_HOURS
def test_thread_needs_same_kind_and_an_arrow(row, kind, fresh, at):
    sid = story(effect=row, kind=kind, at=at)
    item("a", "Panthers preview", text="With Kyren Williams out, the Rams lean on the pass")
    got = tag.run(INDEX, answer=fake(story="new", effect="down", kind="injury", fresh=fresh), now=NOW)
    assert got["new"] == 1 and got["attached"] == 0 and db.story(sid)["items"] == []


@pytest.mark.parametrize("effect,rev", [("up", 2), ("none", 1)])
def test_a_moved_status_bumps_the_rev(effect, rev):
    sid = story()
    item("a", "Kyren Williams back at full practice")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", effect=effect), now=NOW)
    s = db.story(sid)
    assert s["rev"] == rev and got["updated"] == (rev - 1)
    assert rows(sid)[1]["effect"] == ("up" if rev == 2 else "down")


def test_a_raising_item_is_skipped_and_retried():
    a = item("a", "Kyren Williams limited", at="2026-10-19T10:00:00+00:00")
    item("b", "Puka Nacua limited", at="2026-10-19T11:00:00+00:00")
    good = fake()

    def ask(i, *rest, **k):
        if "Kyren" in i["title"]:
            raise KeyError("odd item")
        return good(i, *rest, **k)

    got = tag.run(INDEX, answer=ask, now=NOW)
    assert got["failed"] == [a] and got["tagged"] == 1 and got["error"] is None
    assert [u["id"] for u in db.untagged_items()] == [a]


def test_a_chart_lift_goes_on_the_story_but_not_the_items_names():
    """The depth chart's next man up has a row (▲, depth) but the item never named him: player_ids stays who it names,
    so he never leads the story."""
    base = fake(effect="down")

    def ask(*a, **k):
        out = base(*a, **k)
        out["about:2"], out["subject:2"], out["fresh:2"] = ({"type": "noul", "noul": 1.0} for _ in range(3))
        out["effect:2"] = {"type": "choice", "choice": "up", "probabilities": {"up": 0.75}, "confidence": 0.75}
        out["kind:2"] = {"type": "choice", "choice": "depth", "probabilities": {"depth": 0.75}, "confidence": 0.75}
        out["chart:2"] = {"type": "chart", "behind": 1}
        return out

    item("a", "Kyren Williams ruled out")
    tag.run(INDEX, answer=ask, now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    assert (rows(s["id"])[2]["effect"], rows(s["id"])[2]["kind"]) == ("up", "depth")
    assert s["items"][0]["player_ids"] == [1] and tag._lead(s) == 1


def test_a_threaded_step_carries_his_next_man():
    sid = story(players=(1,))
    base = fake(effect="down")

    def ask(*a, **k):
        out = base(*a, **k)
        out["about:2"], out["subject:2"], out["fresh:2"] = ({"type": "noul", "noul": 1.0} for _ in range(3))
        out["effect:2"] = {"type": "choice", "choice": "up", "probabilities": {"up": 0.75}, "confidence": 0.75}
        out["kind:2"] = {"type": "choice", "choice": "depth", "probabilities": {"depth": 0.75}, "confidence": 0.75}
        out["chart:2"] = {"type": "chart", "behind": 1}
        return out

    item("a", "Kyren Williams on IR")
    tag.run(INDEX, answer=ask, now=NOW)
    assert rows(sid)[2]["effect"] == "up" and rows(sid)[2]["kind"] == "depth"


def test_earlier_item_becomes_first():
    first = item("first", "Kyren Williams hurt", at="2026-10-19T10:00:00+00:00")
    sid = story(first_item=first)
    db.tag_item(first, [1], None, sid, AT)
    earlier = item("b", "Kyren Williams leaves practice", at="2026-10-19T08:00:00+00:00")
    tag.run(INDEX, answer=fake(story=f"s{sid}"), now=NOW)
    s = db.story(sid)
    assert (s["first_item_id"], s["first_at"]) == (earlier, "2026-10-19T08:00:00+00:00")


def test_correction_marks_story_corrected():
    sid = story()
    i = item("a", "Kyren Williams report was wrong", source="bsky:beat")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", retract=0.8), now=NOW)
    s = db.story(sid)
    assert (s["status"], s["rev"], s["status_note"], s["status_at"]) == ("corrected", 2, "bsky:beat", AT)
    assert [x["id"] for x in s["items"]] == [i] and got["updated"] == 1
    assert rows(sid)[1]["effect"] == "up"  # the correction is the current fact


def test_retract_without_a_story_is_a_new_story():
    sid = story(at=OLD)
    item("a", "Kyren Williams report was wrong")
    got = tag.run(INDEX, answer=fake(story="new", retract=0.8), now=NOW)
    assert got["new"] == 1 and db.story(sid)["status"] == "live"


def test_edited_item_retagged_on_own_story():
    sid, other = story(), story()
    i = item("a", "Kyren Williams limited")
    db.tag_item(i, [1], None, sid, "2026-10-19T12:30:00+00:00")
    db.update_item_text(i, "Kyren Williams ruled out", "", "h2", "2026-10-19T14:00:00+00:00")
    ask = fake(story=f"s{other}")
    got = tag.run(INDEX, answer=ask, now=NOW)
    assert [c["id"] for c in ask.calls[0][2]] == [sid]  # its own story is the only candidate
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
    got = tag.run(INDEX, answer=fake(about={1: 0.1}), now=NOW)
    s = db.story(sid)
    assert got["tagged"] == 1 and db.untagged_items() == []
    assert [x["withdrawn_at"] for x in s["items"] if x["id"] == i] == [AT]  # no longer one of its sources
    # with no live source left the story is retracted (and re-surfaces), as a deleted source would leave it
    assert (s["status"], s["rev"]) == (("live", 1) if others else ("retracted", 2))


def test_item_without_players_has_no_story():
    i = item("a", "Rams sign a long snapper")
    ask = fake()
    got = tag.run(INDEX, answer=ask, now=NOW)
    assert ask.calls == [] and got["tagged"] == 1 and got["new"] == 0
    assert db.untagged_items() == []
    with db._read() as c:
        r = c.execute(db.sa.select(db.news_items_table).where(db.news_items_table.c.id == i)).first()
    assert (r.story_id, r.tagged_at, r.player_ids) == (None, AT, [])


def test_not_about_player_dropped():
    i = item("a", "Kyren Williams and Puka Nacua", text="Coach said Puka Nacua the punter, not the receiver")
    tag.run(INDEX, answer=fake(about={2: 0.08}), now=NOW)
    [s] = db.recent_stories([1], "2026-10-01")
    assert set(rows(s["id"])) == {1}
    assert [x["player_ids"] for x in s["items"] if x["id"] == i] == [[1]]


def test_all_players_not_about_leaves_no_story():
    sid = story(players=(3,))
    item("a", "Ben Johnson proud of the line", text="Johnson said the line was great", source="bsky:x", club="CHI")
    got = tag.run(INDEX, answer=fake(story=f"s{sid}", about={3: 0.08}), now=NOW)
    assert got["tagged"] == 1 and got["new"] == 0 and got["attached"] == 0
    assert db.story(sid)["items"] == []
    assert db.untagged_items() == []


def test_media_item_player_from_ext_id():
    i = item("vid#2", "Sean McVay press conference", text="he had a great day, and Kyren Williams ran hard",
             source="yt:LAR")
    ask = fake()
    tag.run(INDEX, answer=ask, now=NOW)
    assert {p["player_id"] for p in ask.calls[0][1]} == {1, 2}
    [s] = db.recent_stories([2], "2026-10-01")
    assert set(rows(s["id"])) == {1, 2} and s["items"][0]["id"] == i


def test_untagged_newest_first():
    a = item("a", "Puka Nacua limited", at="2026-10-19T09:00:00+00:00")
    b = item("b", "Kyren Williams limited", at="2026-10-19T11:00:00+00:00")
    assert [u["id"] for u in db.untagged_items()] == [b, a]


def test_older_item_joining_newer_news_never_changes_or_corrects_it():
    """A backlog is tagged newest first: Saturday's "questionable" joining Monday's "out" story is a source of it,
    not a change to it, so the tagger's changed/retract answers are not acted on and the story keeps its rev."""
    sid = story()
    new = item("new", "Kyren Williams out", at="2026-10-19T11:00:00+00:00")
    db.tag_item(new, [1], None, sid, NOW.isoformat())
    old = item("old", "Kyren Williams questionable", at="2026-10-17T11:00:00+00:00")
    ask = fake(story=f"s{sid}", retract=0.9)
    got = tag.run(INDEX, answer=ask, now=NOW)
    assert got["attached"] == 1 and got["updated"] == 0
    s = db.story(sid)
    assert s["rev"] == 1 and s["status"] == "live" and s["first_item_id"] == old


