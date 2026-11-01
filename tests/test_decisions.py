import json

from ffl import decisions as D
from ffl import season
from ffl.config import ROOT
from tests.home import home_league

def test_every_kind_has_presets_ending_in_other():
    assert set(D.PRESETS) == set(D.KINDS)
    assert all(v[-1][0] == "other" for v in D.PRESETS.values())
    assert "followed_call" in D.preset_keys("lineup") and "followed_call" not in D.preset_keys("claims")


def test_bench_slots_match_season():
    assert D._BENCH == season.BENCH_SLOTS


def test_followed_the_call_shared_cases():
    """The Roster page decides dialog or toast at the tap with web/lib/decisions.ts's copy of these rules; both
    copies run tests/fixtures/followed_call.json, so they cannot drift."""
    f = json.loads((ROOT / "tests" / "fixtures" / "followed_call.json").read_text())
    by = {c["player_id"]: c for c in f["cards"]}
    for case in f["lineup"]:
        assert D.followed_lineup([tuple(m) for m in case["moves"]], f["cards"]) is case["followed"], case["why"]
    for case in f["sit"]:
        assert D.followed_sit(by[case["player_id"]], case["sat"]) is case["followed"], case["why"]
    assert D.followed_sit({"call": "OUT"}, True) is True, "a card with no model_call reads its call"


SAMPLE = """# Decisions log

## YYYY-MM-DD — <short title>
- **Decision:**

---

## 2026-09-06 (late) — Five more practice drafts
Prose only.

## 2026-09-22 — Week 3 waiver claims: Boston
- **Decision:** claim Boston
- **Reasoning:** best add
- **Alternatives rejected:** White
- **Exit conditions:** two quiet games
- **Outcome (2026-09-23 run):** got him
- **Lesson:** one drop per claim

## 2026-10-06 — Waiver claims (from the app)
- **Decision:** (1) get 1 of 1: Vele; cut Egbuka
- **Reasoning:** (add by hand)
- **Outcome (fill in later):**

## 2026-10-06 — Waiver claims (from the app)
- **Decision:** (1) get 1 of 1: Vele; cut Wicks
- **Reasoning:** (add by hand)
- **Outcome (fill in later):**

## 2026-10-08 — Week 5 sit: David Montgomery
- **Decision:** sat
- **Reasoning:** the call was START
- **Outcome (fill in later):**

## 2026-10-08 — Week 5 sit: David Montgomery
- **Decision:** sat
- **Reasoning:** the call was START
- **Outcome (fill in later):**
"""


def test_parse_journal_fields_kinds_and_keys():
    rows = D.parse_journal(SAMPLE)
    assert [(r["kind"], r["title"]) for r in rows] == [
        ("draft", "Five more practice drafts"), ("claims", "Week 3 waiver claims: Boston"),
        ("claims", "Waiver claims (from the app)"), ("sit", "Week 5 sit: David Montgomery"),
        ("sit", "Week 5 sit: David Montgomery")]
    draft, boston, app_claims, sit1, sit2 = rows
    assert draft["at"] == "2026-09-06" and draft["body"].startswith("## 2026-09-06 (late)") and draft["rationale"] == ["other"]
    assert (boston["decision"], boston["rationale_note"], boston["alternatives"], boston["exit_conditions"], boston["outcome"],
            boston["week"]) == ("claim Boston", "best add", "White", "two quiet games", "got him", 3)
    assert "one drop per claim" in boston["body"]
    assert app_claims["decision"].endswith("cut Wicks"), "the last resubmit of the day stands"
    assert app_claims["rationale"] is None and app_claims["rationale_note"] is None and app_claims["outcome"] is None
    assert sit1["facts"] == {"calls": "the call was START"} and sit1["rationale"] is None
    assert sit1["import_key"] != sit2["import_key"], "two real sits on one day are two rows"
    assert all(r["source"] == "import" for r in rows)


def test_singular_exit_condition_label_lands_in_exit_conditions():
    text = "## 2026-09-18 — Mayer to Williams\n- **Exit condition:** drop him after two quiet games\n"
    (row,) = D.parse_journal(text)
    assert row["exit_conditions"] == "drop him after two quiet games"


def test_an_in_season_entry_without_week_in_its_title_takes_its_dates_week():
    text = ("## 2026-09-06 — Pre-draft strategy set\n- **Decision:** RB/WR first\n\n"
            "## 2026-10-02 — Drop Waller, add Mayer\n- **Decision:** add Mayer\n\n"
            "## 2026-10-08 — Week 5 sit: David Montgomery\n- **Decision:** sat\n")
    week = {"2026-10-02": 4}.__getitem__  # the only in-season date without a title week
    assert [r["week"] for r in D.parse_journal(text, week)] == [None, 4, 5], "draft-era stays weekless; a title's week stands"
    assert [r["week"] for r in D.parse_journal(text)] == [None, None, 5], "no week function, no derived week"


def test_validate_rationale():
    assert D.validate_rationale("lineup", ["matchup"], None) is None
    assert D.validate_rationale("lineup", None, None) is None
    assert "not a claims reason" in D.validate_rationale("claims", ["matchup"], None)
    assert "needs a note" in D.validate_rationale("lineup", ["other"], "  ")
    assert "unknown kind" in D.validate_rationale("vibes", ["other"], "x")


def test_cli_add_rationale_outcome_list_export(capsys):
    from ffl import db
    assert D.main(["add", "--kind", "free_add", "--title", "Week 6 add: Tucker", "--decision", "add Tucker, drop Monangai",
                   "--week", "6", "--rationale", "injury_fill", "--players", "1,2"]) == 0
    row = db.decisions(ref=home_league())[0]
    assert (row["source"], row["rationale"], row["player_ids"], row["outcome"]) == ("session", ["injury_fill"], [1, 2], None)
    assert D.main(["add", "--kind", "free_add", "--title", "x", "--rationale", "matchup"]) == 2, "not a roster reason"
    assert D.main(["outcome", str(row["id"]), "8.4 in his first start"]) == 0
    assert db.decision(row["id"], ref=home_league())["outcome"] == "8.4 in his first start" and db.decision(row["id"], ref=home_league())["outcome_at"]
    assert D.main(["rationale", str(row["id"]), "other", "--note", "Jonathan's read"]) == 0
    assert db.decision(row["id"], ref=home_league())["rationale_note"] == "Jonathan's read"
    assert D.main(["rationale", str(row["id"]), "injury_fill,other"]) == 0, "Other keeps the note it has"
    assert db.decision(row["id"], ref=home_league())["rationale"] == ["injury_fill", "other"]
    assert db.decision(row["id"], ref=home_league())["rationale_note"] == "Jonathan's read", "no --note leaves the note as it was"
    capsys.readouterr()
    assert D.main(["list", "--open"]) == 0 and "Week 6 add: Tucker" not in capsys.readouterr().out
    assert D.main(["export"]) == 0
    out = capsys.readouterr().out
    assert "## " in out and "Week 6 add: Tucker" in out and "**Outcome:** 8.4 in his first start" in out
    assert D.main(["outcome", "9999", "x"]) == 2
