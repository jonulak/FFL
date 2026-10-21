"""Player index off ESPN's player list, and the name matcher that finds who a news item names.

False positives are the risk, not misses: a wrong player on a story pushes a notice about someone
who is not in it. So a bare surname or a garbled name counts only among the clubs the item is
about, and a name that fits two players in scope matches neither.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import requests
from espn_api.football.constant import PRO_TEAM_MAP

from .. import news
from ..adp import norm_name
from ..config import SEASON, raw_league_get

PLAYERS_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/players"
INDEX_S = 6 * 3600
# without a filter the public view answers 50 players; a limit needs a sort (400 FILTER_LIMIT_MISSING_SORT otherwise)
PUBLIC_FILTER = {"filterActive": {"value": True}, "limit": 5000,
                 "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}
MIN_INDEX = 500  # fewer kept is a short answer, not the league: treated as a failed fetch
FUZZY = 0.85
# surnames that are also plain words: a bare "Love" or "Hill" in a sentence is far more often the word
COMMON_WORD_SURNAMES = {
    "love", "hill", "brown", "white", "young", "price", "hunt", "wise", "moore", "hall", "green", "gray",
    "rice", "little", "strong", "bell", "long", "smart", "hope", "rich", "ward", "banks", "mills",
    "waddle", "pierce"}
_INDEX: list = []  # [expires_at, Index]


@dataclass
class Index:
    by_id: dict[int, dict] = field(default_factory=dict)  # espn id -> {name, pos, team}
    by_club: dict[str, list[int]] = field(default_factory=dict)
    by_full: dict[str, list[int]] = field(default_factory=dict)  # normalized full name -> ids
    owned: dict[int, float] = field(default_factory=dict)  # espn id -> ESPN's percent owned
    # every named QB/RB/WR/TE/K the list carries, on a club or not: for display only, never matched, so a player
    # tagged on a story and cut since (proTeamId 0) keeps his name on it
    names: dict[int, dict] = field(default_factory=dict)  # espn id -> {name, pos, team (None without a club)}

    def info(self, pid: int) -> dict:
        return self.by_id.get(pid) or self.names.get(pid) or {}


def build_index(players: list[dict]) -> Index:
    """QB/RB/WR/TE/K on a club, from ESPN's player records. The public list has no `active` flag;
    ESPN keeps a `ownership` block only on players it still lists (retired and long-gone players with
    an old club have none), so that stands in for it, and an explicit `active: false` also drops."""
    idx = Index()
    for p in players:
        pos = news.DEFAULT_POS.get(p.get("defaultPositionId"))
        team = PRO_TEAM_MAP.get(p.get("proTeamId"))
        name = p.get("fullName") or ""
        if pos is not None and p.get("id") is not None and name:
            idx.names[int(p["id"])] = {"name": name, "pos": pos, "team": team if team and team != "None" else None}
        if pos is None or p.get("id") is None or not team or team == "None" or not name:
            continue
        if p.get("active") is False or not (p.get("ownership") or p.get("active")):
            continue
        full = norm_name(name)
        if len(full.split()) < 2:
            continue
        pid = int(p["id"])
        idx.by_id[pid] = {"name": name, "pos": pos, "team": team}
        idx.by_club.setdefault(team, []).append(pid)
        idx.by_full.setdefault(full, []).append(pid)
        idx.owned[pid] = float((p.get("ownership") or {}).get("percentOwned") or 0)
    return idx


def _public_players() -> list[dict]:
    r = requests.get(PLAYERS_URL.format(season=SEASON), params={"view": "players_wl"},
                     headers={"x-fantasy-filter": json.dumps(PUBLIC_FILTER)}, timeout=60)
    r.raise_for_status()
    return r.json()


def _league_players() -> list[dict]:
    """Same records through the league's kona view, which needs the league cookies."""
    filters = {"players": {"filterSlotIds": {"value": [0, 2, 4, 6, 17]}, "limit": 1500,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    data = raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filters)})
    return [e["player"] for e in data.get("players") or [] if e.get("player")]


def _checked(idx: Index) -> Index:
    if len(idx.by_id) < MIN_INDEX:
        raise ValueError(f"ESPN's player list came back short: {len(idx.by_id)} players kept")
    return idx


def fetch_index() -> Index:
    """Cached 6 h; the last good index on a failure. Raises only when it has never succeeded."""
    if _INDEX and _INDEX[0] > time.time():
        return _INDEX[1]
    try:
        try:
            idx = _checked(build_index(_public_players()))
        except (requests.RequestException, ValueError):
            idx = _checked(build_index(_league_players()))
    except (Exception, SystemExit):  # noqa: BLE001 — config exits on missing cookies; keep the last good index
        if _INDEX:
            return _INDEX[1]
        raise
    _INDEX[:] = [time.time() + INDEX_S, idx]
    return idx


def _near(m: SequenceMatcher, a: str) -> bool:
    """ratio() >= FUZZY of `a` against the matcher's full name, behind its cheap upper bounds (length, then letters):
    the full ratio of every token pair against every player in scope was nearly all of a tagging run's time. The
    matcher is built once a name, so the name's index is too."""
    la, lb = len(a), len(m.b)
    if not la or 2 * min(la, lb) / (la + lb) < FUZZY:
        return False
    m.set_seq1(a)
    return m.quick_ratio() >= FUZZY and m.ratio() >= FUZZY


_OPENERS = " \t\r\n\"\u201c\u2018'([“"


def _tokens(text: str) -> list[tuple[str, bool]]:
    """Normalized tokens, each with whether a bare surname may stand on it: capitalized in the text
    and not the first word of a sentence."""
    out: list[tuple[str, bool]] = []
    text = text.replace("\u2019", "'")
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text):
        before = text[:m.start()].rstrip(_OPENERS)
        starts = not before or before[-1] in ".!?:" or "\n" in text[len(before):m.start()]
        for t in re.findall(r"[a-z0-9]+", norm_name(m.group())):
            out.append((t, m.group()[0].isupper() and not starts))
    return out


def match(text: str, index: Index, clubs: tuple[str, ...] = ()) -> list[int]:
    """ESPN ids of the players `text` names, by first mention. A full name matches league-wide; a
    surname alone, or a name within a fuzzy ratio of a full one, only among `clubs`; one that fits
    two or more players there matches none of them."""
    pairs = _tokens(text)
    toks = [t for t, _ in pairs]
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
        fuzzy = {p: SequenceMatcher(None, "", full) for p, full in full_of.items()}
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
                near = [p for p, m in fuzzy.items() if _near(m, pair)]
                if near:
                    if len(near) == 1:
                        found.append((i, near[0]))
                    taken[i:i + 2] = [True, True]
                    i += 2
                    continue
            ids = surname.get(toks[i]) if pairs[i][1] and toks[i] not in COMMON_WORD_SURNAMES else None
            if ids and len(ids) == 1:
                found.append((i, ids[0]))
            i += 1

    out: list[int] = []
    for _, p in sorted(found):
        if p not in out:
            out.append(p)
    return out
