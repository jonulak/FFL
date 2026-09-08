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
