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

from . import Cached, Idle, scored
from ..adp import norm_name

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


def parse_page(html: str, pos: str, week: int) -> dict[tuple[str, str], dict]:
    """{(name, pos): {stat: value}}; {} is a page that is not the projections table (broken); Idle is a valid one with
    nothing to project."""
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
    if shown is None or len(groups) != len(names) or not NEEDED[pos] <= set(groups) or "Opp" not in names:
        return {}
    if shown != week:
        raise Idle(f"page shows week {shown}")
    cols = [(i, GROUPS[g][n]) for i, (g, n) in enumerate(zip(groups, names)) if g and n in GROUPS[g]]
    opp = names.index("Opp")
    out, played = {}, 0
    for tr in trs[2:]:
        cells = tr.find_all("td")
        tds = [c.get_text(" ", strip=True) for c in cells]
        if len(tds) != len(names) or not (m := NAME_RE.match(tds[0])):
            continue
        if PLAYED_RE.search(tds[opp]):
            played += 1
            continue
        # the injury tag ("Q", "IR-D") is a span before the name link, so the cell's text reads
        # "Q Jaxon Smith-Njigba WR SEA": the link alone is his name
        link = cells[0].select_one(".player-name a.player-text")
        name = link.get_text(" ", strip=True) if link else m.group(1)
        out[(norm_name(name), pos)] = {stat: _num(tds[i]) for i, stat in cols}
    if not out and played:
        raise Idle("every game played")
    return out


def _position(pos: str, week: int) -> dict[tuple[str, str], dict] | None:
    """None when every page was valid but had nothing to project (Idle)."""
    out: dict[tuple[str, str], dict] = {}
    idle = 0
    for page in range(PAGES[pos]):
        r = requests.get(URL.format(code=POS_CODE[pos], offset=20 * page),
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        r.raise_for_status()
        try:
            out.update(parse_page(r.text, pos, week))
        except Idle:
            idle += 1
    return None if idle == PAGES[pos] else out


def fetch_week(week: int, ks: dict) -> dict[tuple[str, str], float]:
    def fetch():
        with ThreadPoolExecutor(len(POS_CODE)) as pool:
            parts = list(pool.map(lambda p: _position(p, week), POS_CODE))
        if all(p is None for p in parts):
            raise Idle("nothing to project")
        return {k: v for part in parts if part for k, v in part.items()}
    # no per-position minimum: the header checks (week, groups, Opp) already reject reshaped or wrong-week
    # pages, and played games legitimately shrink the page as the week goes on
    # cached as stats, one download for every league; each scores it at read
    return scored(_WEEK.get(str(week), fetch, min_rows={}), ks)
