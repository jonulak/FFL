"""Claude-written weekly outlooks for the Roster page, cached per week under a hash of the facts.

The call and its reason are code (season.roster_cards); this module writes only the paragraph
under them, from those facts alone, by running the Claude Code CLI one player at a time. Nothing
here runs on a GET: generate() is started by POST /outlook/refresh in a background thread, and
the page reads whatever data/outlook/week-<n>.json holds.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from .config import DATA
from .season import CLOSE_SPREAD

OUTLOOK_DIR = DATA / "outlook"
MODEL = "opus"
TIMEOUT_S = 120
SCHEMA = {"type": "object", "properties": {"outlook": {"type": "string"}}, "required": ["outlook"]}
# nothing to say beyond the reason line for these calls; they cost an Opus call for no text
SKIP_CALLS = {"IR", "BYE"}
VOICE = ("Write this player's outlook for the week in two or three sentences, at most sixty words. "
         "Plain statements from the facts above only: no hedging phrases, no advice beyond restating "
         "the call, nothing that is not on this sheet. Do not repeat the kickoff or the opponent. "
         "Third person about the player, written to his manager.")
LOCK = threading.Lock()
# the last generation run, shown on the Roster page beside the button
LAST: dict = {"at": None, "generated": 0, "skipped": 0, "failed": 0}


class Busy(RuntimeError):
    """A generation run is already in progress."""


def fact_hash(card: dict) -> str:
    """The facts the outlook text depends on. A tenth of a point of projection is not one of them."""
    facts = [card.get("opponent"), card.get("kickoff"), bool(card.get("bye")), card.get("injury"),
             card.get("injury_note"), card.get("return_week"), round(float(card.get("proj") or 0.0)),
             float(card.get("spread") or 0.0) >= CLOSE_SPREAD, card.get("call"), bool(card.get("close_call"))]
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()[:16]


def _kickoff_text(card: dict) -> str:
    k = card.get("kickoff")
    if not k:
        return "no game (bye)"
    return datetime.fromisoformat(k).strftime("%A %-I:%M %p")


def fact_sheet(card: dict, week: int) -> str:
    """The prompt: every fact the card shows, then the voice rule."""
    lines = [
        f"Week {week} fact sheet",
        f"Player: {card['name']}, {card['pos']}, {card['team']}",
        f"Opponent: {card.get('opponent') or 'none (bye)'}; kickoff: {_kickoff_text(card)}",
        f"This week's projection: {card.get('proj')} points (ESPN {card.get('proj_espn')}, "
        f"Sleeper {card.get('proj_sleeper')}, spread {card.get('spread')})",
        f"Season projection: {card.get('season_proj')}",
        f"Injury: {card.get('injury')}; note: {card.get('injury_note') or 'none'}; "
        f"return week: {card.get('return_week') or 'n/a'}",
        f"Call: {card['call']}; gap: {card.get('gap')}; close call: {bool(card.get('close_call'))}",
        f"Reason: {card['reason']}",
        "",
        VOICE,
    ]
    return "\n".join(lines)


def _path(week: int) -> Path:
    return OUTLOOK_DIR / f"week-{week}.json"


def load(week: int) -> dict[int, dict]:
    """The week's entries by player id; a missing or corrupt file is an empty week."""
    try:
        raw = json.loads(_path(week).read_text())
    except (OSError, ValueError):
        return {}
    return {int(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}


def save(week: int, entries: dict[int, dict]) -> None:
    OUTLOOK_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(week).with_suffix(".json.tmp")
    tmp.write_text(json.dumps({str(k): v for k, v in entries.items()}, indent=1))
    os.replace(tmp, _path(week))


def attach(cards: list[dict], entries: dict[int, dict]) -> list[dict]:
    """The stored text on each card, flagged stale when the facts have moved since it was written."""
    out = []
    for c in cards:
        e = entries.get(c["player_id"])
        out.append({**c, "outlook": e["text"] if e else None, "outlook_at": e["generated_at"] if e else None,
                    "outlook_stale": bool(e) and e["hash"] != fact_hash(c)})
    return out
