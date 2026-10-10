from ffl import decisions as D
from ffl import season
from ffl.config import ROOT

CARDS = [{"player_id": 1, "name": "A", "pos": "WR", "call": "START", "gap": 2.5},
         {"player_id": 2, "name": "B", "pos": "WR", "call": "BENCH", "gap": -0.1},
         {"player_id": 3, "name": "C", "pos": "RB", "call": "OUT", "gap": None},
         {"player_id": 4, "name": "D", "pos": "WR", "call": "IR", "gap": None}]


def test_every_kind_has_presets_ending_in_other():
    assert set(D.PRESETS) == set(D.KINDS)
    assert all(v[-1][0] == "other" for v in D.PRESETS.values())
    assert "followed_call" in D.preset_keys("lineup") and "followed_call" not in D.preset_keys("claims")


def test_bench_slots_match_season():
    assert D._BENCH == season.BENCH_SLOTS


def test_followed_lineup():
    assert D.followed_lineup([(1, "WR", "RB/WR/TE"), (2, "RB/WR/TE", "WR")], CARDS) is False, "a BENCH call into a start"
    assert D.followed_lineup([(1, "BE", "WR"), (2, "WR", "BE")], CARDS) is True
    assert D.followed_lineup([(1, "WR", "RB/WR/TE")], CARDS) is True, "a START shuffled between starting slots"
    assert D.followed_lineup([(3, "BE", "IR"), (4, "IR", "BE")], CARDS) is True, "OUT to IR, IR activation to the bench"
    assert D.followed_lineup([(1, "WR", "BE")], CARDS) is False, "benching a START"


def test_followed_sit():
    assert D.followed_sit({"call": "START", "model_call": "START"}, True) is False
    assert D.followed_sit({"call": "BENCH", "model_call": "BENCH"}, True) is True
    assert D.followed_sit({"call": "OUT"}, True) is True
    assert D.followed_sit({"call": "SIT", "model_call": "START"}, False) is True, "standing him back up is the call"


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


def test_the_real_journal_parses_every_entry():
    text = (ROOT / "journal" / "DECISIONS.md").read_text()
    rows = D.parse_journal(text)
    headings = [l for l in text.splitlines() if l.startswith("## 20")]
    app_claims = [l for l in headings if l.endswith("Waiver claims (from the app)")]
    assert len(rows) == len(headings) - len(app_claims) + len({l[3:13] for l in app_claims})
    assert len({r["import_key"] for r in rows}) == len(rows)
