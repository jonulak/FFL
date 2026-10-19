"""A transcript cut into one excerpt per player it names.

`players.match` says who a text names, not where, so each seg is matched on its own with the last word
of the seg before it in front: a name split across two segs is still whole, and a surname opening a
seg is judged against the punctuation that came before it. Each mention opens a window from BEFORE_S
before it to AFTER_S after; one player's windows closer than MERGE_S merge, and his excerpt is his
windows' text joined with " … ", starting where his first window starts.
"""
from __future__ import annotations

from .players import Index, match

BEFORE_S = 30.0
AFTER_S = 90.0
MERGE_S = 60.0
JOIN = " … "


def _mentions(segs: list[dict], index: Index, clubs: tuple[str, ...]) -> dict[int, list[float]]:
    """player id -> the starts of the segs he is named in, in first-mention order."""
    out: dict[int, list[float]] = {}
    prev = ""
    for s in segs:
        for pid in match(f"{prev} {s['text']}".strip(), index, clubs):
            out.setdefault(pid, []).append(s["start"])
        words = s["text"].split()
        prev = words[-1] if words else prev
    return out


def _windows(times: list[float]) -> list[list[float]]:
    out: list[list[float]] = []
    for t in sorted(times):
        lo, hi = round(max(0.0, t - BEFORE_S), 3), t + AFTER_S
        if out and lo - out[-1][1] < MERGE_S:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def by_player(segs: list[dict], index: Index, clubs: tuple[str, ...]) -> list[dict]:
    """`{player_id, start, text}` per player named, by first mention."""
    out = []
    for pid, times in _mentions(segs, index, clubs).items():
        wins = _windows(times)
        parts = [" ".join(s["text"] for s in segs if lo <= s["start"] <= hi) for lo, hi in wins]
        out.append({"player_id": pid, "start": wins[0][0], "text": JOIN.join(p for p in parts if p)})
    return out
