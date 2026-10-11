"""FleaFlicker's projections (fleaflicker.com/nfl/leaders, statType 7), rescored to this league.
FleaFlicker shows only the current NFL week, whatever the URL asks, so a page whose header names
another week contributes nothing (ESPN rolls its week on Tuesday; FleaFlicker on its own clock).
Columns are found by their group (Passing, Rushing, Receiving, Misc) and name; a row whose game is
already played carries actual stats and is skipped. parse_page is the pure seam.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import requests
from bs4 import BeautifulSoup

from . import Cached
from ..adp import norm_name
from ..sleeper import score

URL = "https://www.fleaflicker.com/nfl/leaders?statType=7&sortMode=7&position={code}&tableOffset={offset}"
POS_CODE = {"QB": 4, "RB": 1, "WR": 2, "TE": 8}
PAGES = {"QB": 2, "RB": 3, "WR": 3, "TE": 2}   # 20 a page: the top 40-60 cover a 10-team league
GROUPS = {"Passing": {"Yd": "pass_yd", "TD": "pass_td", "INT": "pass_int"},
          "Rushing": {"Yd": "rush_yd", "TD": "rush_td"},
          "Receiving": {"Rec": "rec", "Yd": "rec_yd", "TD": "rec_td"},
          "Misc": {"Fum": "fum_lost"}}
NEEDED = {"QB": {"Passing", "Rushing"}, "RB": {"Rushing", "Receiving"}, "WR": {"Receiving"}, "TE": {"Receiving"}}
NAME_RE = re.compile(r"^(.+?) (QB|RB|WR|TE) [A-Z]{2,3}\b")
PLAYED_RE = re.compile(r"\b[WLT] \d+-\d+")
WEEK_RE = re.compile(r"Week (\d+)")
_WEEK = Cached("fleaflicker")


def _num(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0   # "—"


def parse_page(html: str, pos: str, ks: dict, week: int) -> dict[tuple[str, str], float]:
    table = BeautifulSoup(html, "lxml").select_one("#body-center-main table")
    if table is None:
        return {}
    trs = table.find_all("tr")
    if len(trs) < 3:
        return {}
    groups: list[str] = []
    shown = None
    for th in trs[0].find_all(["th", "td"]):
        text = th.get_text(" ", strip=True)
        if "Projected" in text and (m := WEEK_RE.search(text)):
            shown = int(m.group(1))
        g = next((k for k in GROUPS if k in text), "")
        groups += [g] * int(th.get("colspan") or 1)
    names = [c.get_text(" ", strip=True) for c in trs[1].find_all(["th", "td"])]
    if shown != week or len(groups) != len(names) or not NEEDED[pos] <= set(groups) or "Opp" not in names:
        return {}
    cols = [(i, GROUPS[g][n]) for i, (g, n) in enumerate(zip(groups, names)) if g and n in GROUPS[g]]
    opp = names.index("Opp")
    out = {}
    for tr in trs[2:]:
        tds = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(tds) != len(names) or not (m := NAME_RE.match(tds[0])) or PLAYED_RE.search(tds[opp]):
            continue
        out[(norm_name(m.group(1)), pos)] = round(score({stat: _num(tds[i]) for i, stat in cols}, ks), 1)
    return out


def _position(pos: str, ks: dict, week: int) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for page in range(PAGES[pos]):
        r = requests.get(URL.format(code=POS_CODE[pos], offset=20 * page),
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        out.update(parse_page(r.text, pos, ks, week))
    return out


def fetch_week(week: int, ks: dict) -> dict[tuple[str, str], float]:
    def fetch():
        with ThreadPoolExecutor(len(POS_CODE)) as pool:
            parts = list(pool.map(lambda p: _position(p, ks, week), POS_CODE))
        return {k: v for part in parts for k, v in part.items()}
    return _WEEK.get(week, fetch)
