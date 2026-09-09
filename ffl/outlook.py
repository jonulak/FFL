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
LAST: dict = {"at": None, "generated": 0, "skipped": 0, "failed": 0, "error": None}


class Busy(RuntimeError):
    """A generation run is already in progress."""


def fact_hash(card: dict) -> str:
    """The facts the outlook text depends on. A tenth of a point of projection is not one of them."""
    facts = [card.get("opponent"), card.get("kickoff"), bool(card.get("bye")), card.get("injury"),
             card.get("injury_note"), card.get("return_week"), round(float(card.get("proj") or 0.0)),
             float(card.get("spread") or 0.0) >= CLOSE_SPREAD, card.get("call"), bool(card.get("close_call"))]
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()[:16]


def _kickoff_text(card: dict) -> str:
    """A missing kickoff is only a bye when the card says so — ESPN also leaves it empty
    for a player it has no schedule entry for, and calling that a bye puts a false fact on the sheet."""
    k = card.get("kickoff")
    if not k:
        return "no game (bye)" if card.get("bye") else "not scheduled"
    return datetime.fromisoformat(k).strftime("%A %-I:%M %p")


def _opponent_text(card: dict) -> str:
    o = card.get("opponent")
    if o:
        return o
    return "none (bye)" if card.get("bye") else "not scheduled"


def fact_sheet(card: dict, week: int) -> str:
    """The prompt: every fact the card shows, then the voice rule."""
    lines = [
        f"Week {week} fact sheet",
        f"Player: {card['name']}, {card['pos']}, {card['team']}",
        f"Opponent: {_opponent_text(card)}; kickoff: {_kickoff_text(card)}",
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


def parse_envelope(stdout: str) -> str:
    """The outlook string out of `claude -p --output-format json`.

    The envelope is one JSON object: `is_error` + `result` on failure ("Not logged in …"),
    `structured_output` holding the schema'd object on success; older CLIs put that JSON in
    `result` instead.
    """
    d = json.loads(stdout)
    if d.get("is_error"):
        raise RuntimeError(f"claude: {d.get('result')}")
    out = d.get("structured_output")
    if not isinstance(out, dict):
        out = json.loads(d.get("result") or "{}")
    text = str(out.get("outlook") or "").strip()
    if not text:
        raise RuntimeError("claude returned no outlook")
    return text


def run_claude(prompt: str) -> str:
    """One outlook through the Claude Code CLI: Opus, tools off, structured output, the sheet on stdin."""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not on PATH")
    # Run from inside a Claude Code session, the CLI answers "Not logged in" — its CLAUDE* variables
    # mark a nested session. Dropping them is harmless under launchd, where they never exist.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    r = subprocess.run([exe, "-p", "--bare", "--model", MODEL, "--tools", "", "--no-session-persistence",
                        "--output-format", "json", "--json-schema", json.dumps(SCHEMA)],
                       input=prompt, capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr.strip()[:200]}")
    return parse_envelope(r.stdout)


def pending(cards: list[dict], entries: dict[int, dict]) -> list[dict]:
    """Cards a run would write: not played, not IR/BYE, and no entry with the current hash."""
    out = []
    for c in cards:
        old = entries.get(c["player_id"])
        if c.get("game_played") or c.get("call") in SKIP_CALLS or (old and old["hash"] == fact_hash(c)):
            continue
        out.append(c)
    return out


def generate(cards: list[dict], week: int, run=run_claude, now: datetime | None = None) -> dict:
    """Write text for every pending card, one CLI call each, in order. A failed call keeps the old
    entry and moves on; the file is written once at the end, so a run that dies keeps nothing."""
    entries = load(week)
    todo = pending(cards, entries)
    counts = {"generated": 0, "skipped": len(cards) - len(todo), "failed": 0}
    for c in todo:
        try:
            text = run(fact_sheet(c, week))
        except Exception:  # noqa: BLE001 — a missing CLI, a timeout or bad JSON costs one card, not the run
            counts["failed"] += 1
            continue
        entries[c["player_id"]] = {"hash": fact_hash(c), "text": text,
                                   "generated_at": (now or datetime.now()).isoformat(timespec="minutes")}
        counts["generated"] += 1
    save(week, entries)
    return counts


def running() -> bool:
    return LOCK.locked()


def start(cards: list[dict], week: int, run=run_claude) -> threading.Thread:
    """Run generate() on a daemon thread under LOCK; Busy when one is already running.

    POST /outlook/refresh cannot wait: a week rollover is a dozen serial Opus calls and the
    page's fetch gives up at twenty seconds. LAST carries the finished run's counts to the page.
    """
    if not LOCK.acquire(blocking=False):
        raise Busy("outlook refresh already running")

    def work():
        # Nothing above this thread can see an exception it raises, so a run that dies has to leave
        # its own record: the counts stay at the last finished run's and `error` says this one failed.
        try:
            counts = generate(cards, week, run=run)
            LAST.update(counts, at=datetime.now().isoformat(timespec="minutes"), error=None)
        except Exception as e:  # noqa: BLE001 — the page needs the reason, not a lost traceback
            LAST.update(at=datetime.now().isoformat(timespec="minutes"), error=f"{type(e).__name__}: {e}")
        finally:
            LOCK.release()

    t = threading.Thread(target=work, name="outlook", daemon=True)
    try:
        t.start()
    except Exception:  # noqa: BLE001 — a thread that never ran will never release the lock
        LOCK.release()
        raise
    return t
