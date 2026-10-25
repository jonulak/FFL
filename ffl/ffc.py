"""FantasyFootballCalculator 10-team 2QB draft logs, as replay material.

    uv run python -m ffl.ffc fetch            # newest completed drafts -> the DB's draft_logs (source ffc, kept)
    uv run python -m ffl.ffc fetch --n 60
    uv run python -m ffl.ffc list             # what is stored, and how many human seats each has

FFC runs live 2QB mock rooms — the same rooms its 2QB ADP is computed from — and publishes
every completed draft pick by pick, with the seats people sat in labelled by username and the
rest marked Computer. They are mixed rooms (one to four humans is typical), and the computer
seats draft to FFC's own ADP, which is the feed we use — so the human seats are the ones worth
judging against, and the rest are only good for volume. `ffl.replay --human` honours that.

Rows have the watcher's pick-log shape so `ffl.replay` reads both (`ffc:<id>`): overall, round, pick,
team_id (= slot), player_id (resolved to our player pool by name + position, D/ST by team),
player, pos, nfl, human. Unresolved names keep player_id 0 and are skipped by the replay.
Fetched politely, one page at a time with a pause, and never re-fetched once stored.
"""
from __future__ import annotations

import argparse
import re
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

from . import board as board_mod
from . import db
from .adp import norm_name, records

BASE = "https://fantasyfootballcalculator.com"
INDEX = BASE + "/mock-draft/results/format/2qb/teams/10"
SOURCE = "ffc"  # draft_logs.source
COLUMNS = ["overall", "round", "pick", "team_id", "player_id", "player", "pos", "nfl", "human"]
UA = {"User-Agent": "ffl-replay/1.0 (personal draft prep; one request per few seconds)"}
PAUSE_S = 2.5


def index_ids(html: str) -> list[int]:
    return [int(m) for m in dict.fromkeys(re.findall(r"/draft/(\d+)", html))]


def parse_draft(html: str) -> tuple[list[dict], list[bool]]:
    """Picks in draft order from a draft page, and which slots were human.

    The page is a grid: one column per slot, one row per round; each cell is a player link
    with position and team. Snake order is reconstructed from the round number.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise ValueError("no table on the page")
    head = table.find("thead")
    if head is None:
        raise ValueError("no table header")
    # Two header rows: slot numbers, then "Computer" or the username. A computer seat carries
    # class="computer" on its cell; the round column is class "first-col".
    first = head.find_all("tr")[0]
    slot_cells = [c for c in first.find_all("th") if "first-col" not in (c.get("class") or [])]
    human = ["computer" not in (c.get("class") or []) for c in slot_cells]
    body = table.find("tbody") or table
    picks = []
    n = len(slot_cells)
    rnd = 0
    for tr in body.find_all("tr"):
        cells = [c for c in tr.find_all("td") if "first-col" not in (c.get("class") or [])]
        if len(cells) < n:
            continue
        rnd += 1
        row = []
        for c in cells[:n]:
            a = c.find("a")
            name = (a.get("title") or a.get_text(" ", strip=True)) if a else ""
            txt = c.get_text(" ", strip=True)
            # "RB (DET) 6": the cell's class is the position; the parenthesised code the team
            cls = [k for k in (c.get("class") or []) if k in ("QB", "RB", "WR", "TE", "K", "DEF", "DST", "PK")]
            m = re.search(r"\(([A-Z]{2,3})\)", txt)
            pos = (cls[0] if cls else "").replace("DEF", "D/ST").replace("DST", "D/ST").replace("PK", "K")
            if not pos:
                m2 = re.search(r"\b(QB|RB|WR|TE|K|DEF|DST|PK)\b", txt)
                pos = (m2.group(1) if m2 else "").replace("DEF", "D/ST").replace("DST", "D/ST").replace("PK", "K")
            team = m.group(1) if m else ""
            row.append({"player": name, "pos": pos, "nfl": team})
        order = range(n) if rnd % 2 == 1 else range(n - 1, -1, -1)
        for k, slot in enumerate(order):
            cell = row[slot]
            if not cell["player"]:
                continue
            picks.append({"overall": (rnd - 1) * n + k + 1, "round": rnd, "pick": k + 1,
                          "team_id": slot + 1, **cell})
    return picks, human


TEAM_ALIASES = {"JAC": "JAX", "WAS": "WSH", "LA": "LAR", "KCC": "KC", "GBP": "GB", "NEP": "NE",
                "NOS": "NO", "SFO": "SF", "TBB": "TB", "LVR": "LV", "ARZ": "ARI", "BLT": "BAL",
                "CLV": "CLE", "HST": "HOU"}


def resolve(picks: list[dict], players: pd.DataFrame) -> list[dict]:
    """Attach our player ids by normalised name + position; D/ST by NFL team."""
    pl = players.copy()
    pl["key"] = pl["name"].map(norm_name)
    by_key = {(k, p): int(i) for k, p, i in zip(pl.key, pl.pos, pl.player_id)}
    by_name = {}
    for k, i in zip(pl.key, pl.player_id):
        by_name.setdefault(k, int(i))
    dst = {t: int(i) for t, i, p in zip(pl.team, pl.player_id, pl.pos) if p == "D/ST"}
    for p in picks:
        pid = 0
        if p["pos"] == "D/ST":
            t = TEAM_ALIASES.get(p["nfl"], p["nfl"])
            pid = dst.get(t, 0)
        else:
            key = norm_name(p["player"])
            pid = by_key.get((key, p["pos"])) or by_name.get(key) or 0
        p["player_id"] = pid
    return picks


def fetch(n: int, pause: float = PAUSE_S) -> list[int]:
    settings, players, _ = board_mod.load_inputs()
    r = requests.get(INDEX, headers=UA, timeout=30)
    r.raise_for_status()
    ids = index_ids(r.text)[:n]
    stored = db.draft_log_ids(SOURCE)
    got = []
    for did in ids:
        if str(did) in stored:
            got.append(did)
            continue
        time.sleep(pause)
        url = f"{BASE}/draft/{did}"
        r = requests.get(url, headers=UA, timeout=30)
        if r.status_code != 200:
            print(f"  {did}: HTTP {r.status_code}, skipped")
            continue
        try:
            picks, human = parse_draft(r.text)
        except ValueError as e:
            print(f"  {did}: {e}, skipped")
            continue
        picks = resolve(picks, players)
        for p in picks:
            p["human"] = bool(human[p["team_id"] - 1]) if p["team_id"] - 1 < len(human) else False
        df = pd.DataFrame(picks)[COLUMNS]
        db.put_draft_log(SOURCE, str(did), records(df), {"url": url, "human": [bool(h) for h in human]})
        unresolved = int((df.player_id == 0).sum())
        print(f"  {did}: {len(df)} picks, {sum(human)} human seat(s), {unresolved} unresolved name(s)")
        got.append(did)
    return got


def list_cached():
    rows = []
    for log in db.draft_logs_rows(SOURCE):
        df = pd.DataFrame(log["rows"])
        if not len(df):
            continue
        rows.append({"draft": log["log_id"], "picks": len(df), "rounds": int(df["round"].max()),
                     "human_seats": int(df[df.human.astype(bool)].team_id.nunique()) if "human" in df.columns else 0,
                     "unresolved": int((df.player_id == 0).sum())})
    print(pd.DataFrame(rows).to_string(index=False) if rows else "(nothing stored)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fetch", "list"])
    ap.add_argument("--n", type=int, default=50)
    a = ap.parse_args()
    if a.cmd == "fetch":
        got = fetch(a.n)
        print(f"{len(got)} drafts stored (replay them: uv run python -m ffl.replay ffc --human)")
    else:
        list_cached()


if __name__ == "__main__":
    main()
