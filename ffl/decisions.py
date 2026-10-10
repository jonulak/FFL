"""The decision journal's rules: the kinds, the rationale presets the app offers, and when a move followed the app's
call (so it is recorded without asking). Pure; db.py stores the rows. T5 adds the CLI (`uv run python -m ffl.decisions`)."""
from __future__ import annotations

from datetime import datetime

KINDS = ("lineup", "sit", "unsit", "claims", "free_add", "trade", "draft", "note")

_LINEUP = [("followed_call", "Followed the call"), ("matchup", "Matchup"), ("injury", "Injury/practice report"),
           ("usage", "Usage trend"), ("weather", "Weather"), ("gut", "Gut"), ("other", "Other")]
_ROSTER = [("best_ros", "Best ROS add"), ("injury_fill", "Injury fill-in"), ("bye_cover", "Bye cover"),
           ("stash", "Handcuff/stash"), ("breakout", "Usage breakout"), ("cut_dead", "Cut dead weight"), ("other", "Other")]
_TRADE = [("need", "Positional need"), ("sell_high", "Sell high"), ("buy_low", "Buy low"), ("schedule", "Bye/schedule"),
          ("other", "Other")]
PRESETS = {"lineup": _LINEUP, "sit": _LINEUP, "unsit": _LINEUP, "claims": _ROSTER, "free_add": _ROSTER, "trade": _TRADE,
           "draft": [("other", "Other")], "note": [("other", "Other")]}

_BENCH = {"BE", "IR"}  # season.BENCH_SLOTS; kept here so this module imports nothing


def preset_keys(kind: str) -> set[str]:
    return {k for k, _ in PRESETS[kind]}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def followed_lineup(items: list[tuple[int, str, str]], cards: list[dict]) -> bool:
    """Every moved player lands where his call puts him: a START in a starting slot, anything else off them."""
    call = {c["player_id"]: c["call"] for c in cards}
    return all((call.get(p) == "START") == (to not in _BENCH) for p, _, to in items)


def followed_sit(card: dict, sat: bool) -> bool:
    """Sitting a player the call starts goes against it; standing one back up, or sitting a bench player, does not."""
    return not sat or (card.get("model_call") or card["call"]) != "START"
