"""A transcript cut into one excerpt per player it names.

`players.match` says who a text names, not where, so each seg is matched on its own with the last word
of the seg before it in front: a name split across two segs is still whole, and a surname opening a
seg is judged against the punctuation that came before it. Each mention opens a window from BEFORE_S
before it to AFTER_S after; one player's windows closer than MERGE_S merge, and his excerpt is his
windows' text joined with " … ", starting where his first window starts.

Spoken, players go by first name ("Caleb was sharp"), which `match` never takes alone: written items
keep its rules, and a transcript adds a first-name pass of its own. A capitalized first name that
belongs to exactly one indexed player of `clubs` names him, wherever it stands (caption punctuation is
too unreliable for the sentence-initial rule); a first name that is also a common word, or a name a coach
says of anyone, never does (COMMON_WORD_FIRST_NAMES; the full name or the surname still catches him).
A player named in the video or episode title gets an excerpt too: his windows come from his first or
last name anywhere in the transcript (the title says who it means), else the first TITLE_S.
"""
from __future__ import annotations

import re

from ..adp import norm_name
from .players import Index, match

BEFORE_S = 30.0
AFTER_S = 90.0
MERGE_S = 60.0
TITLE_S = 120.0
JOIN = " … "
COMMON_WORD_FIRST_NAMES = {
    "will", "chase", "drake", "christian", "hunter", "mason", "grant", "austin", "tyler", "jordan", "mike",
    "rich", "royce", "brock", "baker", "josh", "justin", "chris", "jalen", "cooper", "tank", "jameson"}
_WORD = re.compile(r"[A-Z][A-Za-z'’.\-]*")


def _capitalized(text: str) -> list[str]:
    """The text's capitalized words in order, normalized like the index's names."""
    return [norm_name(re.sub(r"'s$", "", w.replace("’", "'").rstrip("."))) for w in _WORD.findall(text)]


def _first_names(index: Index, clubs: tuple[str, ...]) -> dict[str, int]:
    """Normalized first name -> the one player of `clubs` who has it; shared and common-word names are left out."""
    seen: dict[str, list[int]] = {}
    for p in {p for c in clubs for p in index.by_club.get(c, [])}:
        seen.setdefault(norm_name(index.by_id[p]["name"].split()[0]), []).append(p)
    return {n: ps[0] for n, ps in seen.items() if len(ps) == 1 and n not in COMMON_WORD_FIRST_NAMES}


def _mentions(segs: list[dict], index: Index, clubs: tuple[str, ...]) -> dict[int, list[float]]:
    """player id -> the starts of the segs he is named in."""
    firsts = _first_names(index, clubs)
    out: dict[int, list[float]] = {}
    prev = ""
    for s in segs:
        ids = match(f"{prev} {s['text']}".strip(), index, clubs)
        ids += [firsts[w] for w in _capitalized(s["text"]) if w in firsts]
        for pid in dict.fromkeys(ids):
            out.setdefault(pid, []).append(s["start"])
        words = s["text"].split()
        prev = words[-1] if words else prev
    return out


def _named(segs: list[dict], name: str) -> list[float]:
    """Starts of the segs carrying the player's first or last name, capitalized."""
    parts = norm_name(name).split()
    keys = {parts[0], parts[-1]}
    return [s["start"] for s in segs if keys.intersection(_capitalized(s["text"]))]


def _windows(times: list[float]) -> list[list[float]]:
    out: list[list[float]] = []
    for t in sorted(times):
        lo, hi = round(max(0.0, t - BEFORE_S), 3), t + AFTER_S
        if out and lo - out[-1][1] < MERGE_S:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


def by_player(segs: list[dict], index: Index, clubs: tuple[str, ...], title: str = "") -> list[dict]:
    """`{player_id, start, text}` per player named in the transcript or the title, by first mention."""
    found = _mentions(segs, index, clubs)
    for pid in match(title, index, clubs) if title else []:
        if pid not in found:
            found[pid] = _named(segs, index.by_id[pid]["name"])
    out = []
    for pid, times in found.items():
        wins = _windows(times) if times else [[0.0, TITLE_S]]
        parts = [" ".join(s["text"] for s in segs if lo <= s["start"] <= hi) for lo, hi in wins]
        out.append({"player_id": pid, "start": wins[0][0], "text": JOIN.join(p for p in parts if p)})
    return sorted(out, key=lambda o: o["start"])
