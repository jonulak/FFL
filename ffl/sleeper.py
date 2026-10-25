"""Sleeper season projections: the raw answer stored once per season (`sleeper_season`), rescored per league on read.

    uv run python -m ffl.sleeper [league key] [--refresh]   # fetch if not stored (or refetch), print the league's frame

Sleeper answers raw stat lines, so one stored response serves every league: `season_players(ref)` rescores
it with the league's scoring into the frame sleeper_players.csv used to hold.
"""
from __future__ import annotations

import sys

import pandas as pd
import requests

from .adp import csv_frame, norm_name
from .config import SEASON
from .formats import RESCORED
from .settings import load, rescoring

API = "https://api.sleeper.app/projections/nfl/{season}"
POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
TWO_POINT = ("pass_2pt", "rush_2pt", "rec_2pt")
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR"}
DST_NAMES = {"ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears",
             "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
             "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs", "LAC": "Chargers", "LAR": "Rams",
             "LV": "Raiders", "MIA": "Dolphins", "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
             "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks", "SF": "49ers", "TB": "Buccaneers",
             "TEN": "Titans", "WAS": "Commanders"}


def score(st: dict, table: dict) -> float:
    """Raw stats (formats.RESCORED names) scored with one position's table, `settings.rescoring(s)[pos]`:
    signed, so an interception or a lost fumble costs what the league says (ffl-twi). Two-point conversions at
    one rate are counted together before the rate. Summed left to right in a plain loop, the order this was
    always summed in (3.12's sum() compensates, which rounds differently): a projection on an x.x5 boundary
    rounds the same as it did."""
    g = lambda k: float(st.get(k) or 0)
    pts = 0.0
    for k in RESCORED:
        if k in table and k not in TWO_POINT:
            pts += g(k) * table[k]
    rates = {table.get(k, 0.0) for k in TWO_POINT}
    if len(rates) == 1:
        return pts + rates.pop() * (g("pass_2pt") + g("rush_2pt") + g("rec_2pt"))
    for k in TWO_POINT:
        pts += g(k) * table.get(k, 0.0)
    return pts


def fetch_raw(season: int) -> list[dict]:
    """Sleeper's regular-season projections for every player, as it answers them."""
    r = requests.get(API.format(season=season),
                     params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in POS],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.json()


def rescore(raw: list[dict], ks: dict) -> pd.DataFrame:
    """The raw answer scored with `ks` (settings.rescoring: a table per position), one row per (name, position), by projection.
    K and D/ST take Sleeper's standard points."""
    rows = []
    for p in raw:
        pl, st = p.get("player", {}), p.get("stats", {})
        pos = pl.get("position")
        if pos not in POS or not st.get("gp"):
            continue
        team = TEAM_FIX.get(pl.get("team") or "", pl.get("team") or "")
        if pos == "DEF":
            pos, name, proj = "D/ST", f"{DST_NAMES.get(team, team)} D/ST", float(st.get("pts_std") or 0)
        elif pos == "K":
            name, proj = f"{pl.get('first_name','')} {pl.get('last_name','')}".strip(), float(st.get("pts_std") or 0)
        else:
            name, proj = f"{pl.get('first_name','')} {pl.get('last_name','')}".strip(), score(st, ks[pos])
        rows.append({"name": name, "key": norm_name(name), "pos": pos, "team": team, "proj_sleeper": round(proj, 1),
                     "sleeper_gp": st.get("gp"), "adp_2qb_sleeper": st.get("adp_2qb"), "adp_ppr_sleeper": st.get("adp_ppr")})
    df = pd.DataFrame(rows).sort_values("proj_sleeper", ascending=False)
    return df.drop_duplicates(["key", "pos"])


def fetch() -> pd.DataFrame:
    """A fresh fetch this season, scored for the current league (the in-season season lookup reads it live)."""
    return rescore(fetch_raw(SEASON), rescoring(load()))


def stored_raw(season: int, refresh: bool = False) -> list[dict]:
    """The season's stored answer, fetched and stored on first use (or every time with `refresh`)."""
    from . import db
    raw = None if refresh else db.sleeper_season_raw(season)
    if raw is None:
        raw = fetch_raw(season)
        db.put_sleeper_season(season, raw)
    return raw


def season_players(ref=None) -> pd.DataFrame:
    """The league's Sleeper frame, as sleeper_players.csv held it: the stored answer for its season,
    rescored with its scoring."""
    from . import leagues
    ref = ref or leagues.current()
    ks = rescoring(load(ref))
    return csv_frame(rescore(stored_raw(ref.season), ks))


def main(argv: list[str] | None = None):
    from . import leagues
    argv = sys.argv[1:] if argv is None else argv
    keys = [a for a in argv if not a.startswith("--")]
    ref = leagues.get(keys[0]) if keys else leagues.current()
    stored_raw(ref.season, refresh="--refresh" in argv)
    df = season_players(ref)
    print(f"{ref.key} {ref.season}: {len(df)} Sleeper players, scored to the league")
    print(df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
