"""CBS's public rest-of-season projections and the week's page, rescored to this league -> {(key, pos): points}.

One HTML page per position (cbssports.com/fantasy/football/stats/<POS>/<season>/restofseason/
projections/ppr/), the top 100 at the position, each row the raw stats CBS projects for the games
he has left — byes and the weeks of a reported injury are already out (`gp` counts games
remaining), rescored with sleeper.score. QB/RB/WR/TE only: CBS's K page scores a flat 3 per field
goal and its D/ST page is on its own scale entirely (about 2.7 points a game above Sleeper's
standard points, 2026-09-17), so for those two positions ROS is Sleeper's number alone.
parse_page is the pure seam.
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from . import Cached, scoring_key
from ..adp import norm_name
from ..config import SEASON
from ..sleeper import score

URL = "https://www.cbssports.com/fantasy/football/stats/{pos}/{season}/restofseason/projections/ppr/"
WEEK_URL = "https://www.cbssports.com/fantasy/football/stats/{pos}/{season}/{week}/projections/ppr/"
_ROS = Cached("cbs-ros")
_WEEK = Cached("cbs")
PAGES = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE"}
# CBS column key (the sortcol of its header link) -> the stat name sleeper.score reads
STATS = {"passing_yds": "pass_yd", "passing_td": "pass_td", "passing_int": "pass_int",
         "rushing_yds": "rush_yd", "rushing_td": "rush_td", "receiving_rec": "rec",
         "receiving_yds": "rec_yd", "receiving_td": "rec_td", "misc_fl": "fum_lost"}
NEEDED = {"QB": {"passing_yds", "passing_td", "passing_int", "rushing_yds", "rushing_td", "misc_fl"},
          "RB": {"rushing_yds", "rushing_td", "receiving_rec", "receiving_yds", "receiving_td", "misc_fl"},
          "WR": {"receiving_rec", "receiving_yds", "receiving_td", "rushing_yds", "rushing_td", "misc_fl"},
          "TE": {"receiving_rec", "receiving_yds", "receiving_td", "misc_fl"}}
SORTCOL_RE = re.compile(r"sortcol=([^&\"]+)")


def _num(text: str) -> float:
    text = text.strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return 0.0  # CBS writes "—" for a stat it does not project


def parse_page(html: str, pos: str, ks: dict) -> dict[tuple[str, str], dict]:
    """{(normalised name, pos): {"pts": points, "games": games left}} for one position page.

    The header row's sort links name the columns (`sortcol=rushing_yds`), so the parse follows the
    header, not positions. A page missing a column the scorer needs returns {}: a reshaped page
    must cost the second opinion, not put a wrong number under `add`.
    """
    soup = BeautifulSoup(html, "lxml")
    head = soup.select_one("tr.TableBase-headTr")
    if head is None:
        return {}
    cols = []
    for th in head.find_all("th"):
        a = th.find("a", href=True)
        m = SORTCOL_RE.search(a["href"]) if a else None
        cols.append(m.group(1) if m else "")
    if not NEEDED[pos] <= set(cols):
        return {}
    out: dict[tuple[str, str], dict] = {}
    for tr in soup.select("tr.TableBase-bodyTr"):
        tds = tr.find_all("td")
        if len(tds) != len(cols):
            continue
        row = {c: _num(td.get_text()) for c, td in zip(cols, tds) if c != "player"}
        long = tds[0].select_one(".CellPlayerName--long a") or tds[0].find("a")
        if long is None:
            continue
        pts = score({STATS[c]: v for c, v in row.items() if c in STATS}, ks)
        out[(norm_name(long.get_text(strip=True)), pos)] = {"pts": round(float(pts), 1), "games": int(row.get("gp", 0))}
    return out


def _pages(url: str, ks: dict, **fmt) -> dict[tuple[str, str], dict]:
    data: dict[tuple[str, str], dict] = {}
    for pos, page in PAGES.items():
        r = requests.get(url.format(pos=page, season=SEASON, **fmt), headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        data.update(parse_page(r.text, pos, ks))
    return data


def fetch_ros(ks: dict, week: int | None = None) -> dict[tuple[str, str], float]:
    """Every position's rest-of-season page rescored (week unused: the page is already the games left; sources.Cached: last good through an outage)."""
    return _ROS.get(f"ros:{scoring_key(ks)}", lambda: {k: v["pts"] for k, v in _pages(URL, ks).items()})


def fetch_week(week: int, ks: dict) -> dict[tuple[str, str], float]:
    """The week's pages rescored; a row with no game this week (gp 0: bye, ruled out) is left out."""
    return _WEEK.get(f"{week}:{scoring_key(ks)}", lambda: {k: v["pts"] for k, v in _pages(WEEK_URL, ks, week=week).items() if v["games"] > 0})
