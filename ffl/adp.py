"""Consensus ADP from FantasyFootballCalculator's public API, stored per feed (`adp_feeds`).

    uv run python -m ffl.adp [league key]     # fetch both feeds for the league's size and scoring, store, print

FFC has a dedicated 2QB feed, the market for a two-quarterback room. We also pull the 1QB feed
(ppr/half/std chosen from league scoring) so the board can show how far QBs get pushed up in 2QB rooms
versus standard ones, and so a 1QB room can price off it (board.adp_mode). A feed is keyed by
(format, teams, year): leagues of one size and PPR value share it.
"""
from __future__ import annotations

import io
import re
import sys

import pandas as pd
import requests

API = "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
TEAM_FIX = {"JAC": "JAX", "WSH": "WAS", "LA": "LAR"}


def norm_name(name: str) -> str:
    n = name.lower().replace(".", "").replace("'", "").replace("-", " ")
    n = re.sub(r"\s+", " ", n).strip()
    return " ".join(p for p in n.split(" ") if p not in SUFFIXES)


def records(df: pd.DataFrame) -> list[dict]:
    """A frame as JSON-safe rows: native Python values, NaN as None."""
    return [{k: (v.item() if hasattr(v, "item") else v) for k, v in r.items()}
            for r in df.astype(object).where(df.notna(), None).to_dict("records")]


def csv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """The frame as pd.read_csv read it back when the draft inputs were CSV files: same dtypes, an empty
    string read as NaN, a fresh RangeIndex. Every stored draft input is read through it, so the board is
    built from exactly the frames it always was."""
    return pd.read_csv(io.StringIO(df.to_csv(index=False)))


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


def feed_keys(settings: dict) -> dict[str, tuple[str, int, int]]:
    """The league's two feeds, {"2qb"|"1qb": (fmt, teams, year)}: its size, its season, its PPR value."""
    from .settings import key_scoring
    teams, year = settings["team_count"], settings["season"]
    return {"2qb": ("2qb", teams, year),
            "1qb": (one_qb_format(key_scoring(settings["scoring"])["ppr"]), teams, year)}


def load_feed(fmt: str, teams: int, year: int) -> pd.DataFrame:
    """A stored feed as adp_2qb.csv / adp_1qb.csv held it."""
    from . import db
    row = db.adp_feed(fmt, teams, year)
    if row is None:
        raise SystemExit(f"no FFC {fmt} ADP stored for {teams} teams, {year}: run `uv run python -m ffl.adp`")
    return csv_frame(pd.DataFrame(row["rows"]))


def merged(two: pd.DataFrame, one: pd.DataFrame) -> pd.DataFrame:
    """The 2QB feed with the 1QB feed's mean and ranks beside it, as adp.csv held it."""
    m = two.merge(one[["key", "pos", "adp", "adp_rank", "adp_pos_rank"]].rename(
        columns={"adp": "adp_1qb", "adp_rank": "adp_1qb_rank", "adp_pos_rank": "adp_1qb_pos_rank"}),
        on=["key", "pos"], how="outer")
    return csv_frame(m.sort_values("adp", na_position="last"))


def store(ref=None) -> dict[str, pd.DataFrame]:
    """Fetch the league's 2QB and 1QB feeds and store them; returns {"2qb": frame, "1qb": frame}."""
    from . import db, leagues
    from .settings import load
    ref = ref or leagues.current()
    out = {}
    for mode, (fmt, teams, year) in feed_keys(load(ref)).items():
        df = fetch(fmt, teams, year)
        db.put_adp_feed(fmt, teams, year, records(df), df.attrs.get("meta", {}))
        out[mode] = df
    return out


def main(argv: list[str] | None = None):
    from . import leagues
    argv = sys.argv[1:] if argv is None else argv
    ref = leagues.get(argv[0]) if argv else leagues.current()
    from .settings import load
    one_fmt = feed_keys(load(ref))["1qb"][0]
    got = store(ref)
    two, one = got["2qb"], got["1qb"]
    m = merged(two, one)
    print(f"{ref.key}\n2QB: {two.attrs['meta']}\n1QB ({one_fmt}): {one.attrs['meta']}")
    print(f"{len(m)} players stored")
    print(m.head(24)[["adp_rank", "name", "pos", "team", "bye", "adp", "adp_1qb"]].to_string(index=False))


if __name__ == "__main__":
    main()
