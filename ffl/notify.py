"""Push notifications for season events (bead ffl-e9x; design in its DESIGN field).

Detectors turn what the API already sees into notices; each is queued in the DB's `notices` outbox under a key that
names the event, so an event is one notice however many times it is seen, across restarts. The flush (every
`POST /notify/tick`, launchd/com.jonulak.ffl-notify) sends the queue by Web Push outside quiet hours. Pure functions
take parsed inputs and `at`; the I/O is at the bottom. FFL_NOTIFY=0 sends nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import db

log = logging.getLogger(__name__)

ADDS = {"FA ADDED", "WAIVER ADDED"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def move_notices(rows: list[dict], at: str) -> list[dict]:
    """One notice per new transaction row. A trade's sent side is skipped: its received row names the same player."""
    out = []
    for r in rows:
        action = r["action"]
        if action == "TRADE_SENT":
            continue
        if action.startswith("TRADE"):
            kind, body = "trade", f"{r['team']} get {r['player']}"
        else:
            kind, body = "move", f"{r['team']} {'+' if action in ADDS else '−'}{r['player']}"
        out.append({"key": f"move:{r['id']}", "kind": kind, "title": "League move", "body": body,
                    "url": "/transactions", "created_at": at})
    return out


def enqueue_moves(rows: list[dict]) -> int:
    """Queue the new log rows. Never fails the sync that found them: a DB error is logged."""
    try:
        return db.add_notices(move_notices(rows, now()))
    except db.DBError as e:
        log.warning("notify: moves not queued: %s", e)
        return 0


# The statuses that mean a starter will not (or probably will not) play: Questionable stays the call's business.
INJURY_LABEL = {"OUT": "OUT", "DOUBTFUL": "Doubtful", "INJURY_RESERVE": "IR"}
NOT_STARTING = {"BE", "IR"}


def injury_notices(flags: dict[int, dict], week: int, at: str) -> list[dict]:
    """A notice for each starter of mine (a lineup slot, not bench or IR) whose game has not locked and whose status
    is OUT, Doubtful or IR. Keyed by week and status: a status seen again is the same notice, a new one is another."""
    out = []
    for pid, f in flags.items():
        status, slot = f.get("injury"), f.get("slot")
        if status not in INJURY_LABEL or not slot or slot in NOT_STARTING or f.get("locked"):
            continue
        out.append({"key": f"injury:{week}:{pid}:{status}", "kind": "injury",
                    "title": f"{f.get('name') or f'Player {pid}'} {INJURY_LABEL[status]}",
                    "body": f"Starting at {slot}. Tap to make the call.", "url": "/roster", "created_at": at})
    return out


def waiver_notices(run: str | None, prev_run: str | None, pending: list[dict], my_adds: set[int],
                   names: dict[int, str], at: str) -> list[dict]:
    """One notice when ESPN's waiver run moved past the one last seen: the claims pending before it, won (my team added
    the player) or lost. Silent the first time a run is seen (nothing to compare), and with no claims pending."""
    if run is None or prev_run is None or run == prev_run or not pending:
        return []

    def name(pid: int) -> str:
        return names.get(pid) or f"player {pid}"

    won = [c["add"] for c in pending if c["add"] in my_adds]
    lost = [c["add"] for c in pending if c["add"] not in my_adds]
    parts = ([f"Won {', '.join(map(name, won))}."] if won else []) + ([f"Lost {', '.join(map(name, lost))}."] if lost else [])
    return [{"key": f"waivers:{run}", "kind": "waivers", "title": f"Waivers: {len(won)} of {len(pending)} claims won",
             "body": " ".join(parts), "url": "/transactions", "created_at": at}]
