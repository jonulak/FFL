"""What the models read and wrote: each run's stage dict carries its own `usage`; the tagger's (Jev) day's running total
is kept in state `news_jev_usage` as {UTC date: {input, output, calls}}, the last DAYS days of it, which
`GET /news/status` shows. The writer's `claude -p` calls are rows in llm_calls (`GET /llm/usage`)."""
from __future__ import annotations

from datetime import datetime

from .. import db

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
    day = now.date().isoformat()

    def add(days):
        days = {**(days or {}), day: {k: ((days or {}).get(day) or {}).get(k, 0) + run[k] for k in run}}
        return {d: days[d] for d in sorted(days)[-DAYS:]}
    db.update_state(key, add, {})
