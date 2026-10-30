"""Lineup slots and league-wide roster demand, shared by the draft board and the in-season maths.

Kept apart from ffl.board and ffl.draft_watch, which import pandas: ffl.season reads these, and through it the
API would load pandas and numpy (~170 MB) for a few dicts (#472).
"""
from __future__ import annotations

SEASON_WEEKS = 17
FLEX_SPLIT = {"RB/WR/TE": {"RB": 0.5, "WR": 0.4, "TE": 0.1}, "RB/WR": {"RB": 0.55, "WR": 0.45},
              "WR/TE": {"WR": 0.8, "TE": 0.2}, "OP": {"QB": 0.85, "RB": 0.1, "WR": 0.05}}
# Extra players past the starter line that still get rostered (bench depth / injury cover),
# expressed per team so it tracks league size. This sets replacement level, so it decides how
# valuable a position looks.
BENCH_PER_TEAM = {"QB": 0.3, "RB": 0.8, "WR": 0.8, "TE": 0.2, "D/ST": 0.0, "K": 0.0}
# In a 1QB league hardly anyone carries a backup QB. Start two and nearly every team wants a
# QB3, which pushes replacement from ~QB23 down to ~QB28 — a ~47 point swing that was making
# every QB look far cheaper than the 2QB market treats them.
QB_BENCH_PER_TEAM_MULTI = 0.8


def is_flex(slot: str) -> bool:
    return slot == "OP" or ("/" in slot and slot != "D/ST")


def is_multi_qb(settings: dict) -> bool:
    starters = settings["starters"]
    return starters.get("QB", 1) > 1 or "OP" in starters  # 2QB / superflex


def bench_depth(settings: dict) -> dict[str, float]:
    """League-wide bench demand per position, given the league's size and lineup format."""
    teams = settings["team_count"]
    out = {p: v * teams for p, v in BENCH_PER_TEAM.items()}
    if is_multi_qb(settings):
        out["QB"] = QB_BENCH_PER_TEAM_MULTI * teams
    return out


def starter_counts(settings: dict) -> dict[str, float]:
    teams = settings["team_count"]
    counts = {p: 0.0 for p in BENCH_PER_TEAM}
    for slot, n in settings["starters"].items():
        if slot in counts:
            counts[slot] += n * teams
        elif slot in FLEX_SPLIT:
            for p, w in FLEX_SPLIT[slot].items():
                counts[p] += n * teams * w
    return counts
