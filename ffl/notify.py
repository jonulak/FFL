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
