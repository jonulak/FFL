"""What the writer read and wrote: its stage dict carries its own `usage`. Each `claude -p` call is a row in llm_calls
(`GET /llm/usage`)."""
from __future__ import annotations


def zero() -> dict:
    return {"input": 0, "output": 0, "calls": 0}


def add(run: dict, input: int, output: int) -> None:
    """One call into a run's tally."""
    run["input"] += input
    run["output"] += output
    run["calls"] += 1
