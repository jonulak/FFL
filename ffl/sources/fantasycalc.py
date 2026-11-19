"""FantasyCalc's redraft market values (api.fantasycalc.com, fitted from real trades), shown beside the trade sheet's
lineup points and never blended into them. Its numbers are for a format, not the league's own scoring table: QB count,
league size and PPR are all it takes, so a TE premium is not in them. QB/RB/WR/TE only: no K or D/ST.
"""
from __future__ import annotations

import requests

from . import Cached
from .. import config
from ..settings import rescoring
from ..slots import is_multi_qb

URL = "https://api.fantasycalc.com/values/current"
TEAMS = (8, 10, 12, 14)   # any other numTeams answers 200 with the 12-team values
PPR = (0, 0.5, 1)         # likewise for any other ppr
TTL_S = 6 * 3600          # values move daily at most, and this is someone else's free API
_VALUES = Cached("fantasycalc")


def _nearest(options: tuple, x: float):
    return min(options, key=lambda o: abs(o - x))


def league_format(settings: dict) -> dict:
    return {"numQbs": 2 if is_multi_qb(settings) else 1, "numTeams": _nearest(TEAMS, settings["team_count"]),
            "ppr": _nearest(PPR, rescoring(settings)["WR"].get("rec", 0.0))}


def parse(rows: list[dict]) -> dict[tuple[int, str], int]:
    return {(int(r["player"]["espnId"]), r["player"]["position"]): r["value"] for r in rows if r["player"].get("espnId")}


def values(fmt: dict) -> dict[int, int]:
    """{ESPN player id: value} for a league_format, the last good through an outage, {} with none or switched off here."""
    if config.source_off("fantasycalc"):
        return {}

    def fetch():
        r = requests.get(URL, params={"isDynasty": "false", **fmt}, headers={"User-Agent": "FFL"}, timeout=15)
        r.raise_for_status()
        return parse(r.json())
    key = f"{fmt['numQbs']}-{fmt['numTeams']}-{fmt['ppr']}"
    return {pid: v for (pid, _), v in _VALUES.get(key, fetch, ttl=TTL_S).items()}


def totals(give: list[int], get: list[int], market: dict[int, int]) -> dict | None:
    """Plain sums of each side, with no 2-for-1 adjustment (that would be a judgment weight). None with no values at all."""
    if not market:
        return None
    return {"send": sum(market.get(i, 0) for i in give), "get": sum(market.get(i, 0) for i in get),
            "unpriced": sum(i not in market for i in (*give, *get))}
