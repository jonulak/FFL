"""Outlook text: the fact hash, the prompt, the per-week file, and the generation loop (no CLI)."""
import json
import threading

import pytest

from ffl import outlook


def card(**kw):
    base = {"player_id": 1, "name": "Jahmyr Gibbs", "pos": "RB", "team": "DET", "slot": "RB", "bye": False,
            "injury": "ACTIVE", "proj_espn": 21.4, "proj_sleeper": 19.0, "proj": 20.2, "spread": 2.4,
            "espn_only": False, "actual": 0.0, "game_played": 0, "season_proj": 320.5,
            "season_proj_sleeper": 300.0, "return_week": None, "injury_note": None,
            "opponent": "GB", "kickoff": "2026-09-13T13:00:00-04:00",
            "group": "starter", "call": "START", "gap": 6.1, "close_call": False,
            "reason": "starts at RB: 20.2 vs 14.1 for Warren"}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def outlook_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(outlook, "OUTLOOK_DIR", tmp_path / "outlook")
    # start()'s thread looks LAST up by name at call time, so a fresh dict per test keeps runs apart
    monkeypatch.setattr(outlook, "LAST", {"at": None, "generated": 0, "skipped": 0, "failed": 0, "error": None})
    return tmp_path / "outlook"


def test_fact_hash_ignores_a_tenth_of_a_point_but_not_the_news():
    base = outlook.fact_hash(card())
    assert outlook.fact_hash(card(proj=20.4)) == base
    assert outlook.fact_hash(card(spread=2.9)) == base, "spread only matters at CLOSE_SPREAD"
    assert outlook.fact_hash(card(spread=3.0)) != base
    assert outlook.fact_hash(card(injury_note="limited in practice")) != base
    assert outlook.fact_hash(card(call="BENCH")) != base
    assert outlook.fact_hash(card(proj=21.0)) != base, "a whole point changes the text"


def test_fact_sheet_carries_the_facts_and_the_voice_rule():
    sheet = outlook.fact_sheet(card(injury="QUESTIONABLE", injury_note="ankle", return_week=None), 2)
    for s in ("Week 2", "Jahmyr Gibbs, RB, DET", "GB", "Sunday 1:00 PM", "20.2", "21.4", "19.0",
              "QUESTIONABLE", "ankle", "START", "starts at RB", "sixty words"):
        assert s in sheet, s
    assert "rank" not in sheet.lower()


def test_fact_sheet_on_a_bye_says_so():
    sheet = outlook.fact_sheet(card(bye=True, opponent=None, kickoff=None, call="BYE", reason="bye week"), 2)
    assert "none (bye)" in sheet and "no game" in sheet


def test_fact_sheet_without_a_schedule_is_not_a_bye():
    """ESPN leaves opponent/kickoff empty for an unscheduled player too; that is not a bye week."""
    sheet = outlook.fact_sheet(card(bye=False, opponent=None, kickoff=None), 2)
    assert "not scheduled" in sheet
    assert "bye" not in sheet.lower()


def test_load_and_save_round_trip_and_tolerate_a_missing_or_corrupt_file(outlook_dir):
    assert outlook.load(2) == {}
    outlook.save(2, {1: {"hash": "abc", "text": "t", "generated_at": "2026-09-10T09:00"}})
    assert outlook.load(2) == {1: {"hash": "abc", "text": "t", "generated_at": "2026-09-10T09:00"}}
    (outlook_dir / "week-3.json").write_text("{not json")
    assert outlook.load(3) == {}
    assert not list(outlook_dir.glob("*.tmp")), "the temp file is renamed away"


def test_attach_marks_stale_when_the_facts_moved():
    c = card()
    fresh = {1: {"hash": outlook.fact_hash(c), "text": "fine", "generated_at": "2026-09-10T09:00"}}
    old = {1: {"hash": "stale", "text": "old", "generated_at": "2026-09-09T09:00"}}
    a, = outlook.attach([c], fresh)
    assert (a["outlook"], a["outlook_at"], a["outlook_stale"]) == ("fine", "2026-09-10T09:00", False)
    b, = outlook.attach([c], old)
    assert (b["outlook"], b["outlook_stale"]) == ("old", True)
    n, = outlook.attach([c], {})
    assert (n["outlook"], n["outlook_at"], n["outlook_stale"]) == (None, None, False)


def test_generate_skips_matched_played_and_ir_bye_and_keeps_old_text_on_failure(outlook_dir):
    cards = [card(player_id=1), card(player_id=2, game_played=100), card(player_id=3, call="IR", group="ir"),
             card(player_id=4, name="Fails"), card(player_id=5, name="New")]
    outlook.save(2, {1: {"hash": outlook.fact_hash(cards[0]), "text": "same", "generated_at": "x"},
                     4: {"hash": "old", "text": "kept", "generated_at": "y"}})
    seen = []

    def run(prompt):
        seen.append(prompt)
        if "Fails" in prompt:
            raise RuntimeError("claude exited 1")
        return "fresh text"

    from datetime import datetime
    counts = outlook.generate(cards, 2, run=run, now=datetime(2026, 9, 10, 9, 30))
    assert counts == {"generated": 1, "skipped": 3, "failed": 1}
    assert len(seen) == 2 and all("Week 2" in p for p in seen)
    got = outlook.load(2)
    assert got[1]["text"] == "same"
    assert got[4] == {"hash": "old", "text": "kept", "generated_at": "y"}
    assert got[5] == {"hash": outlook.fact_hash(cards[4]), "text": "fresh text", "generated_at": "2026-09-10T09:30"}
    assert 2 not in got and 3 not in got


def test_pending_lists_only_the_cards_a_run_would_write():
    cards = [card(player_id=1), card(player_id=2, game_played=100), card(player_id=3, call="BYE")]
    assert [c["player_id"] for c in outlook.pending(cards, {})] == [1]
    assert outlook.pending(cards, {1: {"hash": outlook.fact_hash(cards[0]), "text": "t", "generated_at": "x"}}) == []


def test_start_runs_in_the_background_under_the_lock_and_records_last(outlook_dir):
    gate = threading.Event()

    def run(prompt):
        gate.wait(5)
        return "done"

    t = outlook.start([card()], 2, run=run)
    assert outlook.running() is True
    with pytest.raises(outlook.Busy):
        outlook.start([card()], 2, run=run)
    gate.set()
    t.join(5)
    assert outlook.running() is False
    assert outlook.LAST["generated"] == 1 and outlook.LAST["at"] is not None
    assert outlook.LAST["error"] is None
    assert outlook.load(2)[1]["text"] == "done"


@pytest.mark.filterwarnings("error")
def test_start_releases_the_lock_when_the_run_raises(outlook_dir, monkeypatch):
    """A dying run must record its error and never let the exception escape the thread."""
    monkeypatch.setattr(outlook, "generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    t = outlook.start([card()], 2, run=lambda p: "x")
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
        outlook.start([card()], 2, run=lambda p: "x")
    assert outlook.running() is False


def test_parse_envelope_reads_structured_output_and_refuses_errors():
    ok = json.dumps({"type": "result", "is_error": False, "result": "{\"outlook\": \"ignored\"}",
                     "structured_output": {"outlook": "  He starts.  "}})
    assert outlook.parse_envelope(ok) == "He starts."
    older = json.dumps({"type": "result", "is_error": False, "result": "{\"outlook\": \"From result\"}"})
    assert outlook.parse_envelope(older) == "From result"
    with pytest.raises(RuntimeError, match="Not logged in"):
        outlook.parse_envelope(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}))
    with pytest.raises(RuntimeError):
        outlook.parse_envelope(json.dumps({"is_error": False, "structured_output": {"outlook": ""}}))


def test_run_claude_invokes_the_cli_exactly(monkeypatch):
    calls = {}

    class R:
        returncode = 0
        stdout = json.dumps({"is_error": False, "structured_output": {"outlook": "ok"}})
        stderr = ""

    def fake_run(args, **kw):
        calls["args"], calls["kw"] = args, kw
        return R()

    monkeypatch.setattr(outlook.shutil, "which", lambda name: "/Users/x/.local/bin/claude")
    monkeypatch.setattr(outlook.subprocess, "run", fake_run)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert outlook.run_claude("the sheet") == "ok"
    assert calls["args"] == ["/Users/x/.local/bin/claude", "-p", "--bare", "--model", "opus", "--tools", "",
                             "--no-session-persistence", "--output-format", "json",
                             "--json-schema", json.dumps(outlook.SCHEMA)]
    assert calls["kw"]["input"] == "the sheet" and calls["kw"]["timeout"] == 120
    assert "CLAUDECODE" not in calls["kw"]["env"]


def test_run_claude_without_the_cli_raises(monkeypatch):
    monkeypatch.setattr(outlook.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="claude CLI"):
        outlook.run_claude("x")
