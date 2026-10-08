"""Outlook text: the fact hash, the prompt, the per-week file, and the generation loop (no CLI)."""
import json
import re
import threading
from datetime import date, datetime

import pytest

from ffl import outlook

TODAY = date(2026, 10, 2)
TAKE = {"label": "Take", "text": "Lead back.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test/1"}]}


def ctx(**kw):
    base = {"depth": {"slot": "RB", "order": 1, "rank": 1, "of": 3},
            "teammates": [{"name": "Jared Goff", "pos": "QB", "rank": 1, "status": "Out",
                           "note": "Goff (thumb) won't play.", "last_news": 1790000000000}],
            "rotowire": [{"id": "64002028", "headline": "Gibbs logged a full practice.", "story": "He is fine.",
                          "published": "2026-09-30T21:10:56Z", "url": "http://m.espn.go.com/wireless/story?storyId=64002028"}],
            "last_news": 1790000001000}
    base.update(kw)
    return base


def card(**kw):
    base = {"player_id": 1, "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET", "slot": "RB", "bye": False,
            "injury": "ACTIVE", "proj_espn": 21.4, "proj_sleeper": 19.0, "proj": 20.2, "spread": 2.4,
            "espn_only": False, "actual": 0.0, "game_played": 0, "season_proj": 320.5,
            "season_proj_sleeper": 300.0, "return_week": None, "injury_note": None,
            "opponent": "GB", "kickoff": "2026-09-13T13:00:00-04:00",
            "group": "starter", "call": "START", "gap": 6.1, "close_call": False,
            "reason": "starts at RB: 20.2 vs 14.1 for Warren", "exp": 3,
            "form": {"rows": [{"week": 3, "pts": 18.4, "snap_pct": 74, "tgt": 2.0, "car": 17.0, "rec": 2.0, "pass_att": 0.0}]}}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def outlook_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(outlook, "OUTLOOK_DIR", tmp_path / "outlook")
    # start()'s thread looks LAST up by name at call time, so a fresh dict per test keeps runs apart
    monkeypatch.setattr(outlook.news, "gather", lambda cards, week: {c["player_id"]: ctx() for c in cards})
    monkeypatch.setattr(outlook, "LAST", {"at": None, "generated": 0, "skipped": 0, "failed": 0,
                                          "error": None, "last_failure": None, "pending": None})
    return tmp_path / "outlook"


def test_fact_sheet_has_the_news_and_no_projections():
    sheet = outlook.fact_sheet(card(injury="QUESTIONABLE", injury_note="ankle"), 5, ctx(), TODAY)
    for s in ("Week 5", "Friday 10/2/2026", "Jahmyr Gibbs, RB, DET", "3 years in the league", "GB",
              "QUESTIONABLE", "ankle", "Week 3: 74% snaps", "17 carries", "18.4 pts",
              "1 of 3", "QB1 Jared Goff: Out", "Goff (thumb) won't play.", "9/30 Gibbs logged a full practice.",
              "storyId=64002028", "background only", "START", "Injury, Usage, Depth, Team and Take",
              "ten days", "https://www.espn.com/nfl/player/gamelog/_/id/1"):
        assert s in sheet, s
    for s in ("20.2", "21.4", "19.0", "320.5", "projection:", "starts at RB"):
        assert s not in sheet, f"{s}: projections and the reason line are already on the card"


def test_fact_sheet_with_nothing_to_report_says_none():
    sheet = outlook.fact_sheet(card(form=None, exp=0), 5, ctx(depth=None, teammates=[], rotowire=[], last_news=None), TODAY)
    assert "rookie" in sheet and "not on Sleeper's chart" in sheet
    assert sheet.count("none") >= 3, "games, teammates and Rotowire each say none"


def test_fact_sheet_bye_and_unscheduled_still_differ():
    assert "none (bye)" in outlook.fact_sheet(card(bye=True, opponent=None, kickoff=None, call="BYE"), 5, ctx(), TODAY)
    assert "not scheduled" in outlook.fact_sheet(card(opponent=None, kickoff=None), 5, ctx(), TODAY)


def test_card_hash_moves_with_card_facts_not_projections():
    base = outlook.card_hash(card())
    assert outlook.card_hash(card(proj=25.0, spread=9.0, ros_proj=400.0, reason="other")) == base
    assert outlook.card_hash(card(injury_note="limited")) != base
    assert outlook.card_hash(card(call="BENCH")) != base
    assert outlook.card_hash(card(form={"rows": [{"week": 4, "pts": 3.0, "snap_pct": 30}]})) != base


def test_fact_hash_moves_with_news_and_the_day():
    base = outlook.fact_hash(card(), ctx(), TODAY)
    assert outlook.fact_hash(card(), ctx(), date(2026, 10, 3)) != base, "rewritten at least daily"
    assert outlook.fact_hash(card(), ctx(last_news=1790000009000), TODAY) != base
    mate = {**ctx()["teammates"][0], "last_news": 1790000009000}
    assert outlook.fact_hash(card(), ctx(teammates=[mate]), TODAY) != base, "a teammate's news rewrites him"
    assert outlook.fact_hash(card(), ctx(rotowire=[]), TODAY) != base
    assert outlook.fact_hash(card(), ctx(depth={"slot": "RB", "order": 2, "rank": 2, "of": 3}), TODAY) != base
    assert outlook.fact_hash(card(proj=30.0), ctx(), TODAY) == base


def test_fact_hash_moves_with_the_prompt(monkeypatch):
    """A rewritten voice rule has to reach the cards already written under the old one."""
    base = outlook.fact_hash(card(), ctx(), TODAY)
    monkeypatch.setattr(outlook, "VOICE", outlook.VOICE + " One more rule.")
    assert outlook.fact_hash(card(), ctx(), TODAY) != base


def test_voice_keeps_injury_for_real_injury_news_only():
    assert "a healthy player gets no Injury bullet" in outlook.VOICE
    assert "never a bullet that only says there is no news" in outlook.VOICE


def test_normalize_orders_drops_and_requires_take():
    src = [{"site": "Rotowire", "date": "10/1", "url": "https://x.test/1"}]
    raw = {"bullets": [TAKE, {"label": "Usage", "text": "Up.", "sources": src},
                       {"label": "Injury", "text": "Fine.", "sources": src},
                       {"label": "Usage", "text": "dup", "sources": src},
                       {"label": "Vibes", "text": "?", "sources": src},
                       {"label": "Team", "text": "No source.", "sources": []},
                       {"label": "Depth", "text": "Bad link.", "sources": [{"site": "x", "date": "", "url": "javascript:1"}]}]}
    got = outlook.normalize(raw)
    assert [b["label"] for b in got] == ["Injury", "Usage", "Take"]
    assert got[1]["text"] == "Up."
    with pytest.raises(RuntimeError, match="no Take"):
        outlook.normalize({"bullets": [{"label": "Usage", "text": "Up.", "sources": src}]})


def test_parse_envelope_reads_structured_bullets_and_refuses_errors():
    ok = json.dumps({"is_error": False, "structured_output": {"bullets": [TAKE]}})
    assert outlook.parse_envelope(ok) == [TAKE]
    old_cli = json.dumps({"is_error": False, "result": json.dumps({"bullets": [TAKE]})})
    assert outlook.parse_envelope(old_cli) == [TAKE]
    with pytest.raises(RuntimeError, match="Not logged in"):
        outlook.parse_envelope(json.dumps({"is_error": True, "result": "Not logged in"}))


def test_attach_shows_bullets_flags_moved_card_facts_and_ignores_paragraphs():
    c = card()
    fresh = {1: {"hash": "h", "card_hash": outlook.card_hash(c), "bullets": [TAKE], "generated_at": "t"}}
    a, = outlook.attach([c], fresh)
    assert (a["outlook"], a["outlook_at"], a["outlook_stale"]) == ([TAKE], "t", False)
    b, = outlook.attach([card(injury_note="new")], fresh)
    assert b["outlook_stale"] is True
    for e in ({"hash": "x", "text": "a paragraph from before", "generated_at": "t"}, {"hash": "x"}, "junk"):
        n, = outlook.attach([c], {1: e})
        assert (n["outlook"], n["outlook_at"], n["outlook_stale"]) == (None, None, False)


def test_pending_writes_ir_and_out_skips_played_and_quiet_byes():
    cards = [card(player_id=1), card(player_id=2, game_played=100), card(player_id=3, call="IR", group="ir"),
             card(player_id=4, call="OUT", injury="OUT"), card(player_id=5, call="BYE", bye=True),
             card(player_id=6, call="BYE", bye=True)]
    quiet = ctx(teammates=[], rotowire=[])
    ctxs = {1: ctx(), 2: ctx(), 3: ctx(), 4: ctx(), 5: quiet, 6: ctx()}
    assert [c["player_id"] for c in outlook.pending(cards, {}, ctxs, TODAY)] == [1, 3, 4, 6]
    same = {1: {"hash": outlook.fact_hash(cards[0], ctxs[1], TODAY)}}
    assert 1 not in [c["player_id"] for c in outlook.pending(cards, same, ctxs, TODAY)]


def test_generate_writes_bullets_and_keeps_the_old_entry_on_failure(outlook_dir):
    cards = [card(player_id=4, name="Fails"), card(player_id=5, name="New")]
    outlook.save(5, {4: {"hash": "old", "card_hash": "c", "bullets": [TAKE], "generated_at": "y"}})
    def run(prompt):
        if "Fails" in prompt:
            raise RuntimeError("claude exited 1")
        return [TAKE]
    counts = outlook.generate(cards, 5, run=run, now=datetime(2026, 10, 2, 9, 30), today=TODAY)
    assert counts == {"generated": 1, "skipped": 0, "failed": 1, "last_failure": "RuntimeError: claude exited 1",
                      "pending": 2}
    got = outlook.load(5)
    assert got[4]["hash"] == "old"
    assert got[5] == {"hash": outlook.fact_hash(cards[1], ctx(), TODAY), "card_hash": outlook.card_hash(cards[1]),
                      "bullets": [TAKE], "generated_at": "2026-10-02T09:30"}
    outlook.generate([card(player_id=6, name="Aware")], 5, run=run)
    assert re.search(r"T\d\d:\d\d[+-]\d\d:\d\d$", outlook.load(5)[6]["generated_at"])


def test_generate_runs_calls_side_by_side(outlook_dir):
    """Two calls must be in flight at once: serial calls never meet at the barrier."""
    meet = threading.Barrier(2, timeout=5)
    def run(prompt):
        meet.wait()
        return [TAKE]
    counts = outlook.generate([card(player_id=1), card(player_id=2, name="Other")], 5, run=run, today=TODAY)
    assert counts["generated"] == 2 and counts["failed"] == 0


def test_generate_saves_each_card_as_it_lands(outlook_dir, monkeypatch):
    monkeypatch.setattr(outlook, "WORKERS", 1)  # one at a time, so the second call can see the first on disk
    seen = []
    def run(prompt):
        seen.append(set(outlook.load(5)))
        if "Dies" in prompt:
            raise RuntimeError("timeout")
        return [TAKE]
    counts = outlook.generate([card(player_id=1), card(player_id=2, name="Dies"), card(player_id=3, name="Last")],
                              5, run=run, today=TODAY)
    assert seen == [set(), {1}, {1}], "card 1 was on disk before the next call started"
    assert set(outlook.load(5)) == {1, 3} and counts["failed"] == 1 and counts["pending"] == 3
    assert not list(outlook_dir.glob("*.tmp"))


def test_start_records_the_pending_count(outlook_dir):
    t = outlook.start([card()], 2, run=lambda p: [TAKE])
    t.join(5)
    assert outlook.LAST["pending"] == 1


def test_load_and_save_round_trip_and_an_empty_week(outlook_dir):
    assert outlook.load(2) == {}
    outlook.save(2, {1: {"hash": "abc", "text": "t", "generated_at": "2026-09-10T09:00"}})
    assert outlook.load(2) == {1: {"hash": "abc", "text": "t", "generated_at": "2026-09-10T09:00"}}


def test_start_runs_in_the_background_under_the_lock_and_records_last(outlook_dir):
    gate = threading.Event()

    def run(prompt):
        gate.wait(5)
        return [TAKE]

    t = outlook.start([card()], 2, run=run)
    assert outlook.running() is True
    with pytest.raises(outlook.Busy):
        outlook.start([card()], 2, run=run)
    gate.set()
    t.join(5)
    assert outlook.running() is False
    assert outlook.LAST["generated"] == 1 and outlook.LAST["at"] is not None
    assert outlook.LAST["error"] is None
    assert outlook.load(2)[1]["bullets"] == [TAKE]


@pytest.mark.filterwarnings("error")
def test_start_releases_the_lock_when_the_run_raises(outlook_dir, monkeypatch):
    """A dying run must record its error and never let the exception escape the thread."""
    monkeypatch.setattr(outlook, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    t = outlook.start([card()], 2, run=lambda p: [TAKE])
    t.join(5)
    assert outlook.running() is False
    assert outlook.LAST["error"].startswith("RuntimeError")
    assert outlook.LAST["at"] is not None


def test_start_releases_the_lock_when_the_thread_will_not_start(outlook_dir, monkeypatch):
    """A thread that never ran will never release the lock: refresh would answer 409 forever."""
    class DeadThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(outlook.threading, "Thread", DeadThread)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        outlook.start([card()], 2, run=lambda p: [TAKE])
    assert outlook.running() is False


def test_run_claude_surfaces_the_cli_message_on_a_non_zero_exit(monkeypatch):
    class R:
        returncode = 1
        stdout = json.dumps({"is_error": True, "result": "Not logged in · Please run /login"})
        stderr = ""

    monkeypatch.setattr(outlook.shutil, "which", lambda name: "/Users/x/.local/bin/claude")
    monkeypatch.setattr(outlook.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="Not logged in"):
        outlook.run_claude("x")

    class Bare:
        returncode = 1
        stdout = "garbage"
        stderr = "boom"

    monkeypatch.setattr(outlook.subprocess, "run", lambda *a, **k: Bare())
    with pytest.raises(RuntimeError, match="exited 1: boom"):
        outlook.run_claude("x")


def test_run_claude_without_the_cli_raises(monkeypatch):
    monkeypatch.setattr(outlook.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="claude CLI"):
        outlook.run_claude("x")


def test_run_claude_invokes_the_cli_with_web_tools(monkeypatch):
    calls = {}
    class R:
        returncode = 0
        stdout = json.dumps({"is_error": False, "structured_output": {"bullets": [TAKE]}})
        stderr = ""
    def fake_run(args, **kw):
        calls["args"], calls["kw"] = args, kw
        return R()
    monkeypatch.setattr(outlook.shutil, "which", lambda name: "/Users/x/.local/bin/claude")
    monkeypatch.setattr(outlook.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert outlook.run_claude("the sheet") == [TAKE]
    assert calls["args"] == ["/Users/x/.local/bin/claude", "-p", "--model", "opus",
                             "--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch",
                             "--no-session-persistence", "--output-format", "json",
                             "--json-schema", json.dumps(outlook.SCHEMA)]
    assert "--bare" not in calls["args"]
    assert calls["kw"]["input"] == "the sheet" and calls["kw"]["timeout"] == 300
    assert "CLAUDECODE" not in calls["kw"]["env"]
    assert calls["kw"]["cwd"] == str(outlook.OUTLOOK_DIR)


# --- free agents: one outlook at a time, on a click, from the Waivers page ---

def fa(**kw):
    """A waiver row: the card's facts without the call, with the waiver numbers beside them."""
    row = {k: v for k, v in card(**kw).items() if k not in ("call", "group", "gap", "close_call", "reason", "slot")}
    return {**row, "add": 14.2, "ros_proj": 120.0}


@pytest.fixture
def writing(monkeypatch):
    s: dict[int, str] = {}
    monkeypatch.setattr(outlook, "_WRITING", s)
    return s


def test_free_agent_sheet_frames_the_claim_and_keeps_projections_out():
    sheet = outlook.fact_sheet(fa(), 5, ctx(), TODAY, free_agent=True)
    assert "free agent" in sheet and "weighing a claim" in sheet
    assert "Start/bench call" not in sheet and "None" not in sheet
    assert "what adding him would mean" in sheet
    assert "your own read of what it all means for him" not in sheet, "the roster Take rule is swapped out"
    for s in ("14.2", "120.0", "20.2"):
        assert s not in sheet
    assert "Injury, Usage, Depth, Team and Take" in sheet and "a healthy player gets no Injury bullet" in sheet


def test_waiver_outlooks_keep_their_own_store(outlook_dir):
    outlook.save(5, {1: {"bullets": [TAKE]}}, kind=outlook.WAIVERS)
    assert outlook.load(5) == {}, "the roster run never sees a free agent's entry"
    assert outlook.load(5, kind=outlook.WAIVERS)[1]["bullets"] == [TAKE]


def test_start_one_writes_his_outlook_and_waiver_entry_reads_it(outlook_dir, writing):
    gate, prompts = threading.Event(), []

    def run(prompt):
        prompts.append(prompt)
        gate.wait(5)
        return [TAKE]

    assert outlook.waiver_entry(fa(), 5) == {"bullets": None, "at": None, "stale": False, "running": False, "error": None}
    t = outlook.start_one(fa(), 5, run=run)
    assert outlook.waiver_entry(fa(), 5)["running"] is True
    with pytest.raises(outlook.Busy):
        outlook.start_one(fa(), 5, run=run)
    gate.set()
    t.join(5)
    e = outlook.waiver_entry(fa(), 5)
    assert e["bullets"] == [TAKE] and e["at"] and e["running"] is False and e["error"] is None
    assert e["stale"] is False and "weighing a claim" in prompts[0]
    assert outlook.waiver_entry(fa(injury_note="limited Thursday"), 5)["stale"] is True
    assert outlook.load(5) == {}, "the roster file is untouched"


def test_start_one_keeps_the_old_bullets_and_records_a_failure(outlook_dir, writing):
    outlook.start_one(fa(), 5, run=lambda p: [TAKE]).join(5)
    outlook.start_one(fa(), 5, run=lambda p: (_ for _ in ()).throw(RuntimeError("Not logged in"))).join(5)
    e = outlook.waiver_entry(fa(), 5)
    assert e["bullets"] == [TAKE] and e["error"] == "RuntimeError: Not logged in" and e["running"] is False
    outlook.start_one(fa(), 5, run=lambda p: [TAKE]).join(5)
    assert outlook.waiver_entry(fa(), 5)["error"] is None, "a good write clears the failure"


def test_start_one_caps_the_calls_in_flight(outlook_dir, writing):
    gate = threading.Event()
    threads = [outlook.start_one(fa(player_id=i), 5, run=lambda p: (gate.wait(5), [TAKE])[1])
               for i in range(1, outlook.WORKERS + 1)]
    with pytest.raises(outlook.Busy):
        outlook.start_one(fa(player_id=99), 5, run=lambda p: [TAKE])
    gate.set()
    for t in threads:
        t.join(5)
    assert set(outlook.load(5, kind=outlook.WAIVERS)) == set(range(1, outlook.WORKERS + 1)), "parallel saves all land"
    assert not writing


def test_start_one_frees_the_slot_when_the_thread_will_not_start(outlook_dir, writing, monkeypatch):
    class DeadThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(outlook.threading, "Thread", DeadThread)
    with pytest.raises(RuntimeError):
        outlook.start_one(fa(), 5, run=lambda p: [TAKE])
    assert not writing


# --- one Roster card, on a click from its footer ---

def test_start_one_rewrites_a_card_into_the_roster_file_with_its_fact_hash(outlook_dir, writing):
    gate = threading.Event()
    sheets = []
    t = outlook.start_one(card(), 5, run=lambda p: (sheets.append(p), gate.wait(5), [TAKE])[2], kind=outlook.ROSTER)
    assert outlook.roster_entry(card(), 5)["outlook_running"] is True
    gate.set()
    t.join(5)
    e = outlook.load(5)[1]
    assert e["bullets"] == [TAKE] and e["hash"] == outlook.fact_hash(card(), ctx(), date.today())
    assert not outlook.load(5, outlook.WAIVERS), "a card never lands in the free agents' file"
    assert outlook.FA_TAKE not in sheets[0], "a card's sheet is the call's, not a claim's"
    r = outlook.roster_entry(card(), 5)
    assert (r["outlook"], r["outlook_running"], r["outlook_error"]) == ([TAKE], False, None)
    assert outlook.pending([card()], outlook.load(5), {1: ctx()}, date.today()) == [], "the hourly run skips him"


def test_a_card_rewrite_that_fails_keeps_the_bullets_and_shows_the_reason(outlook_dir, writing):
    outlook.start_one(card(), 5, run=lambda p: [TAKE], kind=outlook.ROSTER).join(5)
    outlook.start_one(card(), 5, run=lambda p: (_ for _ in ()).throw(RuntimeError("Not logged in")),
                      kind=outlook.ROSTER).join(5)
    a, = outlook.attach([card()], outlook.load(5))
    assert a["outlook"] == [TAKE] and "Not logged in" in a["outlook_error"]


def test_start_one_failure_merges_error_into_old_entry(outlook_dir, writing):
    outlook.save(5, {1: {"hash": "h", "card_hash": "c", "bullets": [TAKE], "generated_at": "t"}})

    def boom(sheet):
        raise RuntimeError("cli down")

    outlook.start_one(card(), 5, run=boom, kind=outlook.ROSTER).join(5)
    e = outlook.load(5)[1]
    assert e["bullets"] == [TAKE] and e["error"].startswith("RuntimeError")


def test_a_card_rewrite_and_the_full_run_never_overlap(outlook_dir, writing):
    gate = threading.Event()
    t = outlook.start([card(player_id=2)], 5, run=lambda p: (gate.wait(5), [TAKE])[1])
    with pytest.raises(outlook.Busy, match="full outlook run"):
        outlook.start_one(card(), 5, run=lambda p: [TAKE], kind=outlook.ROSTER)
    gate.set()
    t.join(5)
    gate.clear()
    one = outlook.start_one(card(), 5, run=lambda p: (gate.wait(5), [TAKE])[1], kind=outlook.ROSTER)
    with pytest.raises(outlook.Busy, match="being written"):
        outlook.start([card(player_id=2)], 5, run=lambda p: [TAKE])
    assert not outlook.running(), "a refused run gives the lock back"
    gate.set()
    one.join(5)
    assert set(outlook.load(5)) == {1, 2}, "neither write lost the other's card"
