"""Draft for me from a written queue, for practice runs and as a keyboard-away backup.

    FFL_DB_URL=postgresql+psycopg:///ffl_mock FFL_DATA_DIR=data-mock uv run python -m ffl.autodraft queue.txt
    uv run python -m ffl.autodraft queue.txt --live      # the real draft: opt in explicitly

For the current league (FFL_LEAGUE, else the home league). Watches the watcher's state for it (the
DB's draft_states row); when it says I am on the clock it drafts the first
player in the queue who is still available, falling back to the candidate table's own order.
`ffl.draft_pick` does the clicking and verifies against the socket feed.

**This is not judgment.** The queue is written by a person (or Claude) against the league's strategy (`uv run python -m ffl.strategy show`)
between turns; this module only clicks. Nothing here ranks players — the fallback is
`recommend()`'s existing order, unchanged. See CLAUDE.md: no new weights in code.

Queue file format — one `<player_id> <name>` per line, best first, `#` comments allowed:

    4429795 Jahmyr Gibbs          # applies at every turn
    @37                           # ...until a header: these apply only at overall pick 37
    4689114 Jaxson Dart
    @90-120                       # a range of picks
    3052587 Baker Mayfield

Re-read before every pick, so it can be edited mid-draft.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime

import sqlalchemy as sa
from pathlib import Path

from . import db, leagues
from .draft_pick import PlayerGone, pick

POLL_S = 1.0
# how many candidates to try before giving the turn up: a player can go between our reading
# the table and clicking, and the practice clock is 30s
MAX_ATTEMPTS = 6


def parse_queue(text: str, current: int) -> list[tuple[int, str]]:
    """The queue lines that apply at overall pick `current` (see the module docstring)."""
    out, active = [], True
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        if line.startswith("@"):
            lo, _, hi = line[1:].partition("-")
            try:
                active = int(lo) <= current <= int(hi or lo)
            except ValueError:
                active = False
            continue
        if not active:
            continue
        pid, _, name = line.partition(" ")
        if pid.isdigit() and name.strip():
            out.append((int(pid), name.strip()))
    return out


def drafted_ids(ref=None) -> set[int]:
    """Every player the league's pick feed has seen go (None: the current league)."""
    try:
        return {p["playerId"] for p in db.draft_feed(ref)[0]}
    except db.DBError:
        return set()


def candidates(state: dict, queue: list[tuple[int, str]], ref=None) -> list[tuple[int, str]]:
    """Queue first, then the candidate table's own order; drafted and blacklisted removed."""
    gone = drafted_ids(ref)
    out = [(pid, nm) for pid, nm in queue if pid not in gone]
    for r in state.get("recommend", {}).get("top", []):
        # -999 is recommend()'s blacklist (a position at the league's cap, a 2nd K/DST or non-flex backup,
        # K/DST too early)
        if r["score"] > -900 and r["player_id"] not in gone:
            entry = (r["player_id"], r["name"])
            if entry not in out:
                out.append(entry)
    return out


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _state(ref) -> dict | None:
    try:
        return db.draft_state(ref)
    except db.DBError:  # a busy DB mid-draft: ask again next poll
        return None


def my_turn(state: dict | None, last_pick: int | None) -> int | None:
    """The overall pick I am on the clock for, by the watcher's state; None when it is not my turn, the turn is
    the one already taken (`last_pick`), the draft is over, or no watcher has written a state yet."""
    if not state or state.get("drafted") or not state.get("on_clock_is_me"):
        return None
    current = state.get("current_pick")
    return None if current == last_pick else current


def run(queue_path: Path, timeout_s: float = 22.0, ref=None):
    ref = ref or leagues.current()
    last_pick = None
    log(f"watching the draft state of {ref.key} — queue {queue_path}")
    while True:
        state = _state(ref)
        if state and state.get("drafted"):
            log("draft complete")
            return
        current = my_turn(state, last_pick)
        if current is None:
            time.sleep(POLL_S)
            continue
        queue = parse_queue(queue_path.read_text(), current) if queue_path.exists() else []
        opts = candidates(state, queue, ref)
        log(f"ON THE CLOCK #{current}: trying {[n for _, n in opts[:MAX_ATTEMPTS]]}")
        for pid, name in opts[:MAX_ATTEMPTS]:
            try:
                log("  " + pick(pid, name, timeout_s=timeout_s, ref=ref))
                last_pick = current
                break
            except PlayerGone as e:
                log(f"  gone: {e}")
            except Exception as e:
                # don't hammer a turn we cannot make: the clock has moved on
                log(f"  FAILED {name}: {e}")
                last_pick = current
                break
        else:
            last_pick = current
            log("  every candidate was gone — turn passed to ESPN")
        time.sleep(2)


MOCK_DB = "ffl_mock"  # the database `scripts/devdb.sh mock` makes


def practice_db(url: str | None) -> bool:
    """True when the URL's database is exactly the mock's: the only one this may draft on without --live."""
    if not url:
        return False
    try:
        return sa.engine.make_url(url).database == MOCK_DB
    except sa.exc.ArgumentError:
        return False


if __name__ == "__main__":
    ap =argparse.ArgumentParser()
    ap.add_argument("queue", nargs="?", default="queue.txt", help="queue file (may be absent)")
    ap.add_argument("--live", action="store_true",
                    help="required to run against the real league (FFL_DB_URL's database is not exactly ffl_mock)")
    ap.add_argument("--timeout", type=float, default=22.0, help="seconds to spend per pick")
    a = ap.parse_args()
    if not practice_db(os.environ.get("FFL_DB_URL")) and not a.live:
        raise SystemExit("This drafts for real. Point FFL_DB_URL at the mock database (scripts/devdb.sh mock --no-env) "
                         "for a practice run, or pass --live if you mean the real draft.")
    run(Path(a.queue), a.timeout)
