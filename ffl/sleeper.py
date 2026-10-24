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
from .settings import key_scoring, load

API = "https://api.sleeper.app/projections/nfl/{season}"
POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR"}
DST_NAMES = {"ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears",
             "CIN": "Bengals", "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers",
             "HOU": "Texans", "IND": "Colts", "JAX": "Jaguars", "KC": "Chiefs", "LAC": "Chargers", "LAR": "Rams",
             "LV": "Raiders", "MIA": "Dolphins", "MIN": "Vikings", "NE": "Patriots", "NO": "Saints", "NYG": "Giants",
             "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks", "SF": "49ers", "TB": "Buccaneers",
             "TEN": "Titans", "WAS": "Commanders"}


def score(st: dict, ks: dict) -> float:
    g = lambda k: float(st.get(k) or 0)
    # ks["int"] is INTT's points, signed (settings.key_scoring; ffl-twi)
    return (g("pass_yd") * ks["pass_yd"] + g("pass_td") * ks["pass_td"] + g("pass_int") * ks["int"]
            + g("rush_yd") * ks["rush_yd"] + g("rush_td") * ks["rush_td"]
            + g("rec") * ks["ppr"] + g("rec_yd") * ks["rec_yd"] + g("rec_td") * ks["rec_td"]
            - g("fum_lost") * abs(ks["fumble"]) + 2 * (g("pass_2pt") + g("rush_2pt") + g("rec_2pt")))


def fetch_raw(season: int) -> list[dict]:
    """Sleeper's regular-season projections for every player, as it answers them."""
    r = requests.get(API.format(season=season),
                     params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in POS],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.json()


def rescore(raw: list[dict], ks: dict) -> pd.DataFrame:
    """The raw answer scored with `ks` (settings.key_scoring), one row per (name, position), by projection.
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
            name, proj = f"{pl.get('first_name','')} {pl.get('last_name','')}".strip(), score(st, ks)
        rows.append({"name": name, "key": norm_name(name), "pos": pos, "team": team, "proj_sleeper": round(proj, 1),
                     "sleeper_gp": st.get("gp"), "adp_2qb_sleeper": st.get("adp_2qb"), "adp_ppr_sleeper": st.get("adp_ppr")})
    df = pd.DataFrame(rows).sort_values("proj_sleeper", ascending=False)
    return df.drop_duplicates(["key", "pos"])


def fetch() -> pd.DataFrame:
    """A fresh fetch this season, scored for the current league (the in-season season lookup reads it live)."""
    return rescore(fetch_raw(SEASON), key_scoring(load()["scoring"]))


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
    ks = key_scoring(load(ref)["scoring"])
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
