"""FantasySharks projections (fantasysharks.com/apps/bert/forecasts), weekly and rest of season,
rescored to this league from the CSV export's raw stats. The Segment number picks the period: a
season's base (874 for 2026, read off the page's Period list) + 8 + week, and base + 3 for "Rest of
Year". Columns are read by header name; the TD-distance buckets are ignored (this league scores a TD
the same at any distance). parse_csv is the pure seam.
"""
from __future__ import annotations

import csv
import io

import requests

from . import Cached
from ..adp import norm_name
from ..config import SEASON
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
_ROS = Cached("sharks-ros")


def segment(week: int | str, season: int = SEASON) -> int:
    if season not in BASE:
        raise ValueError(f"no FantasySharks segment base for {season}")
    return BASE[season] + 3 if week == "ros" else BASE[season] + 8 + int(week)


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


def fetch_week(week: int, ks: dict) -> dict[tuple[str, str], float]:
    return _WEEK.get(week, lambda: _fetch(segment(week), ks))


def fetch_ros(ks: dict) -> dict[tuple[str, str], float]:
    return _ROS.get("ros", lambda: _fetch(segment("ros"), ks))
