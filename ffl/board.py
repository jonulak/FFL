"""Build a league-tuned draft board: ESPN projections + 2QB ADP -> data/board.csv, data/board.md."""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .adp import norm_name
from .config import DATA
from .settings import key_scoring, load

FLEX_SPLIT = {"RB/WR/TE": {"RB": 0.5, "WR": 0.4, "TE": 0.1}, "RB/WR": {"RB": 0.55, "WR": 0.45},
              "WR/TE": {"WR": 0.8, "TE": 0.2}, "OP": {"QB": 0.85, "RB": 0.1, "WR": 0.05}}
# extra players past the starter line that still carry value (bench depth / injury replacement)
BENCH_DEPTH = {"QB": 3, "RB": 8, "WR": 8, "TE": 2, "D/ST": 0, "K": 0}
TIER_GAP = {"QB": 12, "RB": 12, "WR": 12, "TE": 10, "D/ST": 6, "K": 4}


def starter_counts(settings: dict) -> dict[str, float]:
    teams = settings["team_count"]
    counts = {p: 0.0 for p in BENCH_DEPTH}
    for slot, n in settings["starters"].items():
        if slot in counts:
            counts[slot] += n * teams
        elif slot in FLEX_SPLIT:
            for p, w in FLEX_SPLIT[slot].items():
                counts[p] += n * teams * w
    return counts


def load_inputs():
    settings = load()
    players = pd.read_csv(DATA / "espn_players.csv")
    adp = pd.read_csv(DATA / "adp.csv")
    return settings, players, adp


def build(settings: dict, players: pd.DataFrame, adp: pd.DataFrame, drafted_ids: set[int] | None = None) -> pd.DataFrame:
    df = players.copy()
    df["key"] = df["name"].map(norm_name)
    # D/ST names differ across sources ("Eagles D/ST" vs "Philadelphia Defense"); match on team instead
    adp_pl = adp[adp.pos != "D/ST"]
    df = df.merge(adp_pl[["key", "pos", "adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank", "bye"]],
                  on=["key", "pos"], how="left")
    adp_dst = adp[adp.pos == "D/ST"][["team", "adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank", "bye"]]
    if len(adp_dst):
        dst = df.pos == "D/ST"
        m = df.loc[dst, ["team"]].merge(adp_dst, on="team", how="left")
        for c in adp_dst.columns[1:]:
            df.loc[dst, c] = m[c].values

    # players only in the 1QB feed (e.g. a TE outside the 2QB top-250): use 1QB ADP as a stand-in
    df["adp_only_1qb"] = df.adp.isna() & df.adp_1qb.notna()
    df["adp"] = df.adp.fillna(df.adp_1qb)
    df["adp_rank"] = df.adp_rank.fillna(df.adp_1qb_rank)
    if drafted_ids:
        df["drafted"] = df.player_id.isin(drafted_ids)
    else:
        df["drafted"] = False
    avail = df[~df.drafted]

    # replacement level from the *available* pool so it stays honest mid-draft
    counts = starter_counts(settings)
    repl = {}
    for pos, n in counts.items():
        pool = avail[avail.pos == pos].sort_values("proj", ascending=False).proj.values
        idx = int(math.ceil(n + BENCH_DEPTH[pos])) - 1 if drafted_ids is None else max(int(math.ceil(n)) - 1 - _taken(df, pos), 0)
        idx = min(max(idx, 0), max(len(pool) - 1, 0))
        repl[pos] = float(pool[idx]) if len(pool) else 0.0
    df["repl"] = df.pos.map(repl)
    df["vorp"] = (df.proj - df.repl).round(1)
    df = df.sort_values(["vorp", "proj"], ascending=False).reset_index(drop=True)
    df["board_rank"] = np.where(df.drafted, np.nan, (~df.drafted).cumsum())
    df["pos_rank"] = df.groupby("pos").proj.rank(ascending=False, method="first").astype(int)
    df["value_vs_adp"] = (df.adp_rank - df.board_rank).round(0)  # + means market lets him fall past his value
    # blended rank: half projection-driven VORP, half what 2QB rooms actually do. Unranked-by-ADP players
    # get a pessimistic ADP so they don't float up on projection alone.
    adp_fill = df.adp_rank.fillna(df.board_rank + 40)
    df["blend"] = ((df.board_rank + adp_fill) / 2).round(1)
    df.loc[df.drafted, "blend"] = np.nan
    df = df.sort_values(["drafted", "blend", "vorp"], ascending=[True, True, False]).reset_index(drop=True)
    df["blend_rank"] = np.where(df.drafted, np.nan, (~df.drafted).cumsum())

    # tiers per position by projection gaps
    df["tier"] = 0
    for pos in counts:
        sub = df[df.pos == pos].sort_values("proj", ascending=False)
        tier, prev = 1, None
        tiers = []
        for v in sub.proj.values:
            if prev is not None and prev - v >= TIER_GAP[pos]:
                tier += 1
            tiers.append(tier)
            prev = v
        df.loc[sub.index, "tier"] = tiers
    return df


def _taken(df: pd.DataFrame, pos: str) -> int:
    return int(((df.pos == pos) & df.drafted).sum())


def to_markdown(df: pd.DataFrame, settings: dict, top_n: int = 40) -> str:
    ks = key_scoring(settings["scoring"])
    L = [f"# Draft board: {settings['name']} {settings['season']}", "",
         f"{settings['team_count']} teams | starters {settings['starters']} | bench {settings['bench_size']} | "
         f"PPR={ks['ppr']} passTD={ks['pass_td']} passYd={ks['pass_yd']} INT={ks['int']}", "",
         "ADP = FantasyFootballCalculator 2QB drafts; ADP1 = same site's 1QB drafts. value = ADP rank minus board rank (positive = tends to fall past his value).", ""]
    cols = ["blend_rank", "board_rank", "name", "pos", "team", "bye", "proj", "vorp", "tier", "adp", "adp_1qb", "value_vs_adp", "injury"]
    a = df[~df.drafted]
    L += ["## Overall (blend of VORP rank and 2QB ADP rank)", "", a.head(top_n)[cols].to_markdown(index=False), ""]
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
        sub = a[a.pos == pos]
        n = {"QB": 32, "RB": 45, "WR": 50, "TE": 20, "D/ST": 14, "K": 12}[pos]
        L += [f"## {pos} (replacement ≈ {sub.repl.iloc[0]:.0f} pts)" if len(sub) else f"## {pos}", "",
              sub.head(n)[["pos_rank", "blend_rank", "tier", "name", "team", "bye", "proj", "vorp", "adp", "adp_1qb", "value_vs_adp", "injury"]].to_markdown(index=False), ""]
    return "\n".join(L)


def main():
    settings, players, adp = load_inputs()
    df = build(settings, players, adp)
    df.to_csv(DATA / "board.csv", index=False)
    (DATA / "board.md").write_text(to_markdown(df, settings))
    unmatched = df[df.adp.isna() & (df.proj > 60)][["name", "pos", "team", "proj"]]
    print(f"{len(df)} players -> data/board.csv, data/board.md")
    print(f"starter counts: {starter_counts(settings)}")
    print(f"replacement: {df.groupby('pos').repl.first().round(0).to_dict()}")
    print(f"\nProjected >60 pts but no ADP match ({len(unmatched)}):\n{unmatched.head(20).to_string(index=False)}")
    print("\nTop 30:\n" + df.head(30)[["blend_rank", "board_rank", "name", "pos", "proj", "vorp", "tier", "adp", "adp_1qb"]].to_string(index=False))


if __name__ == "__main__":
    main()
