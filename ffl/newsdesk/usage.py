"""What the models read and wrote: each run's stage dict carries its own `usage`, and the day's running total is kept in
state (`news_haiku_usage` for the writer, `news_jev_usage` for the tagger) as {UTC date: {input, output, calls}}, the
last DAYS days of it. `GET /news/status` shows both."""
from __future__ import annotations

from datetime import datetime

from .. import db

HAIKU = "news_haiku_usage"
JEV = "news_jev_usage"
DAYS = 30


def zero() -> dict:
    return {"input": 0, "output": 0, "calls": 0}


def add(run: dict, input: int, output: int) -> None:
    """One call into a run's tally."""
    run["input"] += input
    run["output"] += output
    run["calls"] += 1


def record(key: str, run: dict, now: datetime) -> None:
    """A run's tally into the day's total. Raises db.DBError."""
    if not run["calls"]:
        return
    days = db.get_state(key, {}) or {}
    day = now.date().isoformat()
    days[day] = {k: (days.get(day) or {}).get(k, 0) + run[k] for k in run}
    db.put_state(key, {d: days[d] for d in sorted(days)[-DAYS:]})
