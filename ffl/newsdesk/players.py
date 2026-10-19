"""Player index off Sleeper's bulk dump, and the name matcher that finds who a news item names.

False positives are the risk, not misses: a wrong player on a story pushes a notice about someone
who is not in it. So a bare surname or a garbled name counts only among the clubs the item is
about, and a name that fits two players in scope matches neither.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import requests

from .. import news
from ..adp import norm_name

POSITIONS = {"QB", "RB", "WR", "TE", "K"}
INDEX_S = 6 * 3600
FUZZY = 0.85
_INDEX: list = []  # [expires_at, Index]


@dataclass
class Index:
    by_id: dict[int, dict] = field(default_factory=dict)  # espn id -> {name, pos, team}
    by_club: dict[str, list[int]] = field(default_factory=dict)
    by_full: dict[str, list[int]] = field(default_factory=dict)  # normalized full name -> ids


def build_index(sleeper: dict) -> Index:
    """Skill players and kickers who are active, on a club, and have an ESPN id (the id the rest of
    the app keys on). Club codes are ESPN's."""
    idx = Index()
    for v in sleeper.values():
        pos, team, espn = v.get("position"), v.get("team"), v.get("espn_id")
        if pos not in POSITIONS or not team or not espn or not v.get("active"):
            continue
        name = v.get("full_name") or ""
        full = norm_name(name)
        if len(full.split()) < 2:
            continue
        pid, team = int(espn), news.SLEEPER_TO_ESPN.get(team, team)
        idx.by_id[pid] = {"name": name, "pos": pos, "team": team}
        idx.by_club.setdefault(team, []).append(pid)
        idx.by_full.setdefault(full, []).append(pid)
    return idx


def fetch_index() -> Index:
    """Cached 6 h; the last good index on a failure. Raises only when it has never succeeded."""
    if _INDEX and _INDEX[0] > time.time():
        return _INDEX[1]
    try:
        r = requests.get(news.SLEEPER_PLAYERS, timeout=60)
        r.raise_for_status()
        idx = build_index(r.json())
    except (requests.RequestException, ValueError):
        if _INDEX:
            return _INDEX[1]
        raise
    _INDEX[:] = [time.time() + INDEX_S, idx]
    return idx


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm_name(text))


def match(text: str, index: Index, clubs: tuple[str, ...] = ()) -> list[int]:
    """ESPN ids of the players `text` names, by first mention. A full name matches league-wide; a
    surname alone, or a name within a fuzzy ratio of a full one, only among `clubs`; one that fits
    two or more players there matches none of them."""
    toks = _tokens(text)
    taken = [False] * len(toks)
    found: list[tuple[int, int]] = []  # (token position, id)

    longest = max((len(k.split()) for k in index.by_full), default=0)
    for n in range(longest, 1, -1):
        for i in range(len(toks) - n + 1):
            if any(taken[i:i + n]):
                continue
            ids = index.by_full.get(" ".join(toks[i:i + n]))
            if ids and len(ids) > 1:
                ids = [p for p in ids if index.by_id[p]["team"] in clubs]
            if ids and len(ids) == 1:
                found.append((i, ids[0]))
                taken[i:i + n] = [True] * n

    scope = [p for c in clubs for p in index.by_club.get(c, [])]
    if scope:
        full_of = {p: norm_name(index.by_id[p]["name"]) for p in scope}
        surname: dict[str, list[int]] = {}
        for p, full in full_of.items():
            surname.setdefault(full.split()[-1], []).append(p)
        i = 0
        while i < len(toks):
            if taken[i]:
                i += 1
                continue
            pair = " ".join(toks[i:i + 2])
            if i + 1 < len(toks) and not taken[i + 1]:
                near = [p for p, full in full_of.items()
                        if SequenceMatcher(None, pair, full).ratio() >= FUZZY]
                if near:
                    if len(near) == 1:
                        found.append((i, near[0]))
                    taken[i:i + 2] = [True, True]
                    i += 2
                    continue
            ids = surname.get(toks[i])
            if ids and len(ids) == 1:
                found.append((i, ids[0]))
            i += 1

    out: list[int] = []
    for _, p in sorted(found):
        if p not in out:
            out.append(p)
    return out
