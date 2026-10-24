"""Consensus ADP from FantasyFootballCalculator's public API -> data/adp.csv.

FFC has a dedicated 2QB feed, which is the right baseline for this league. We also
pull the 1QB feed (ppr/half/std chosen from league scoring) so the board can show
how far QBs get pushed up in 2QB rooms versus standard ones.
"""
from __future__ import annotations

import re
import sys

import pandas as pd
import requests

from .config import DATA

API = "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR"}


def norm_name(name: str) -> str:
    n = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    n = re.sub(r"\s+", " ", n).strip()
    return " ".join(p for p in n.split(" ") if p not in SUFFIXES)


def fetch(fmt: str, teams: int, year: int) -> pd.DataFrame:
    r = requests.get(API.format(fmt=fmt), params={"teams": teams, "year": year}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("status") != "Success":
        raise SystemExit(f"FFC ADP {fmt}: {d}")
    rows = []
    for p in d["players"]:
        pos = "D/ST" if p["position"] in ("DEF", "DST") else p["position"]
        rows.append({
            "name": p["name"], "key": norm_name(p["name"]), "pos": pos,
            "team": TEAM_FIX.get(p.get("team", ""), p.get("team", "")), "bye": p.get("bye"),
            "adp": float(p["adp"]), "adp_min": p.get("low"), "adp_max": p.get("high"),
            "adp_stdev": p.get("stdev"), "times_drafted": p.get("times_drafted"),
        })
    df = pd.DataFrame(rows).sort_values("adp").reset_index(drop=True)
    df["adp_rank"] = range(1, len(df) + 1)
    df["adp_pos_rank"] = df.groupby("pos").cumcount() + 1
    df.attrs["meta"] = d.get("meta", {})
    return df


def one_qb_format(ppr_points: float) -> str:
    if ppr_points >= 1:
        return "ppr"
    if ppr_points > 0:
        return "half-ppr"
    return "standard"


def main():
    teams = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    year = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    ppr = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    if len(sys.argv) == 1:
        from .settings import key_scoring, load
        try:
            s = load()
        except SystemExit:   # no league settings stored yet: the defaults above
            s = None
        if s:
            teams, year, ppr = s["team_count"], s["season"], key_scoring(s["scoring"])["ppr"]

    two = fetch("2qb", teams, year)
    one = fetch(one_qb_format(ppr), teams, year)
    two.to_csv(DATA / "adp_2qb.csv", index=False)
    one.to_csv(DATA / "adp_1qb.csv", index=False)
    merged = two.merge(one[["key", "pos", "adp", "adp_rank", "adp_pos_rank"]].rename(
        columns={"adp": "adp_1qb", "adp_rank": "adp_1qb_rank", "adp_pos_rank": "adp_1qb_pos_rank"}),
        on=["key", "pos"], how="outer")
    merged = merged.sort_values("adp", na_position="last")
    merged.to_csv(DATA / "adp.csv", index=False)
    print(f"2QB: {two.attrs['meta']}\n1QB ({one_qb_format(ppr)}): {one.attrs['meta']}")
    print(f"{len(merged)} players -> data/adp.csv")
    print(merged.head(24)[["adp_rank", "name", "pos", "team", "bye", "adp", "adp_1qb"]].to_string(index=False))


if __name__ == "__main__":
    main()
