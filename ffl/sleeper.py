"""Sleeper season projections, rescored to this league's settings -> data/sleeper_players.csv."""
from __future__ import annotations

import requests

import pandas as pd

from .adp import norm_name
from .config import DATA, SEASON
from .settings import key_scoring, load

API = f"https://api.sleeper.app/projections/nfl/{SEASON}"
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
    return (g("pass_yd") * ks["pass_yd"] + g("pass_td") * ks["pass_td"] - g("pass_int") * abs(ks["int"])
            + g("rush_yd") * ks["rush_yd"] + g("rush_td") * ks["rush_td"]
            + g("rec") * ks["ppr"] + g("rec_yd") * ks["rec_yd"] + g("rec_td") * ks["rec_td"]
            - g("fum_lost") * abs(ks["fumble"]) + 2 * (g("pass_2pt") + g("rush_2pt") + g("rec_2pt")))


def fetch() -> pd.DataFrame:
    ks = key_scoring(load()["scoring"])
    r = requests.get(API, params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in POS],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    rows = []
    for p in r.json():
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


def main():
    df = fetch()
    df.to_csv(DATA / "sleeper_players.csv", index=False)
    print(f"{len(df)} players -> data/sleeper_players.csv")
    print(df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
