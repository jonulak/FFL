"""FantasySharks projections (fantasysharks.com/apps/bert/forecasts), weekly and rest of season,
rescored to this league from the CSV export's raw stats. The Segment number picks the week: a
season's base (874 for 2026, read off the page's Period list) + 8 + week. Rest of season is the
weekly projections summed over weeks w..18 (all weeks or nothing), like Sleeper's: the page's own
"Rest of Year" segment (base + 3) covers only ~10 games, not the games left, so it is not used.
Columns are read by header name; the TD-distance buckets are ignored (this league scores a TD
the same at any distance). parse_csv is the pure seam.
"""
from __future__ import annotations

import csv
import io
from concurrent.futures import ThreadPoolExecutor

import requests

from . import Cached, Thin, scoring_key
from ..adp import norm_name
from ..config import SEASON
from ..season import NFL_WEEKS
from ..sleeper import score

URL = ("https://www.fantasysharks.com/apps/bert/forecasts/projections.php?csv=1&Sort=&League=-1"
       "&Position={code}&scoring=1&Segment={segment}&uid=4")
POS_CODE = {"QB": 1, "RB": 2, "WR": 4, "TE": 5}
BASE = {2026: 874}   # next season: read the "<season> NFL Season" option value off the page
COLS = {"Pass Yds": "pass_yd", "Pass TDs": "pass_td", "Int": "pass_int", "Rush Yds": "rush_yd",
        "Rush TDs": "rush_td", "Rec": "rec", "Rec Yds": "rec_yd", "Rec TDs": "rec_td", "Fum Lost": "fum_lost"}
NEEDED = {"QB": {"Pass Yds", "Pass TDs", "Int", "Rush Yds", "Rush TDs"},
          "RB": {"Rush Yds", "Rush TDs", "Rec", "Rec Yds", "Rec TDs"},
          "WR": {"Rec", "Rec Yds", "Rec TDs"}, "TE": {"Rec", "Rec Yds", "Rec TDs"}}
_WEEK = Cached("sharks")
_ROS = Cached("sharks-ros")   # its own health: a missing far week must not hide behind weekly successes
FUTURE_TTL_S = 6 * 3600   # weeks after the first barely move between polls


def segment(week: int, season: int = SEASON) -> int:
    if season not in BASE:
        raise ValueError(f"no FantasySharks segment base for {season}")
    return BASE[season] + 8 + int(week)


def _num(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return 0.0


def _name(s: str) -> str:
    last, _, first = s.partition(", ")
    return norm_name(f"{first} {last}" if first else last)


def parse_csv(text: str, pos: str, ks: dict) -> dict[tuple[str, str], float]:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}
    head = rows[0]
    if "Player Name" not in head or not NEEDED[pos] <= set(head):
        return {}
    idx: dict[str, int] = {}
    for i, h in enumerate(head):
        idx.setdefault(h, i)   # ">= 50yds" repeats in the RB files; only the named stats are read
    out = {}
    for r in rows[1:]:
        if len(r) != len(head):
            continue
        st = {stat: _num(r[idx[h]]) for h, stat in COLS.items() if h in idx}
        out[(_name(r[idx["Player Name"]]), pos)] = round(score(st, ks), 1)
    return out


def _fetch(seg: int, ks: dict) -> dict[tuple[str, str], float]:
    data: dict[tuple[str, str], float] = {}
    for pos, code in POS_CODE.items():
        r = requests.get(URL.format(code=code, segment=seg), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        data.update(parse_csv(r.text, pos, ks))
    return data


def fetch_week(week: int, ks: dict, ttl: float | None = None) -> dict[tuple[str, str], float]:
    return _WEEK.get(f"{week}:{scoring_key(ks)}", lambda: _fetch(segment(week), ks), ttl=ttl)


def fetch_ros(ks: dict, week: int) -> dict[tuple[str, str], float]:
    """Weekly projections summed over weeks week..NFL_WEEKS. A week that comes back empty makes the
    whole ROS {} (never an undercount) and a "sharks-ros" failure; a player absent from a week (bye,
    injury) just sums the rest."""
    weeks = list(range(week, NFL_WEEKS + 1))
    if not weeks:
        return {}

    def total():
        with ThreadPoolExecutor(min(6, len(weeks))) as pool:
            per_week = list(pool.map(lambda w: fetch_week(w, ks, None if w == week else FUTURE_TTL_S), weeks))
        if (gone := [w for w, d in zip(weeks, per_week) if not d]):
            raise Thin(f"week {', '.join(map(str, gone))} missing")
        out: dict[tuple[str, str], float] = {}
        for d in per_week:
            for k, v in d.items():
                out[k] = out.get(k, 0.0) + v
        return {k: round(v, 1) for k, v in out.items()}
    return _ROS.get(f"{week}:{scoring_key(ks)}", total)
