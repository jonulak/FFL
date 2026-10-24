"""Build a league-tuned draft board from the DB's draft inputs (ESPN + Sleeper projections, FFC ADP), on demand.

    uv run python -m ffl.board [league key]    # print the board (markdown); nothing is written
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

from . import adp as adp_mod, leagues, players as players_mod, sleeper as sleeper_mod
from .adp import norm_name
from .settings import key_scoring, load

FLEX_SPLIT = {"RB/WR/TE": {"RB": 0.5, "WR": 0.4, "TE": 0.1}, "RB/WR": {"RB": 0.55, "WR": 0.45},
              "WR/TE": {"WR": 0.8, "TE": 0.2}, "OP": {"QB": 0.85, "RB": 0.1, "WR": 0.05}}
# Extra players past the starter line that still get rostered (bench depth / injury cover),
# expressed per team so it tracks league size. This sets replacement level, so it decides how
# valuable a position looks.
BENCH_PER_TEAM = {"QB": 0.3, "RB": 0.8, "WR": 0.8, "TE": 0.2, "D/ST": 0.0, "K": 0.0}
# In a 1QB league hardly anyone carries a backup QB. Start two and nearly every team wants a
# QB3, which pushes replacement from ~QB23 down to ~QB28 — a ~47 point swing that was making
# every QB look far cheaper than the 2QB market treats them.
QB_BENCH_PER_TEAM_MULTI = 0.8
TIER_GAP = {"QB": 8, "RB": 10, "WR": 10, "TE": 9, "D/ST": 6, "K": 4}


def bench_depth(settings: dict) -> dict[str, float]:
    """League-wide bench demand per position, given the league's size and lineup format."""
    teams = settings["team_count"]
    out = {p: v * teams for p, v in BENCH_PER_TEAM.items()}
    if is_multi_qb(settings):
        out["QB"] = QB_BENCH_PER_TEAM_MULTI * teams
    return out


def starter_counts(settings: dict) -> dict[str, float]:
    teams = settings["team_count"]
    counts = {p: 0.0 for p in BENCH_PER_TEAM}
    for slot, n in settings["starters"].items():
        if slot in counts:
            counts[slot] += n * teams
        elif slot in FLEX_SPLIT:
            for p, w in FLEX_SPLIT[slot].items():
                counts[p] += n * teams * w
    return counts


def is_multi_qb(settings: dict) -> bool:
    starters = settings["starters"]
    return starters.get("QB", 1) > 1 or "OP" in starters  # 2QB / superflex


def adp_mode(settings: dict) -> str:
    """Which FFC feed is the market: "2qb" for a room that starts two quarterbacks (QB 2, or an OP slot),
    else "1qb".

    2QB ADP is badly wrong in a one-QB room (public ESPN mocks are all Standard): quarterbacks look wildly
    mispriced, and every survival probability with them. `FFL_ADP_MODE=1qb` (or `2qb`) overrides the
    league's lineup, for a mock in a room that is not the league's; any other value is ignored.
    """
    env = (os.environ.get("FFL_ADP_MODE") or "").lower()
    if env in ("1qb", "2qb"):
        return env
    return "2qb" if is_multi_qb(settings) else "1qb"


def load_adp(settings: dict) -> pd.DataFrame:
    """The league's ADP table, with `adp`/`adp_rank` set to whichever feed `adp_mode()` names.

    2QB: the 2QB feed with the 1QB feed's mean and ranks merged beside it (adp_1qb*). 1QB: the 1QB feed
    alone, its own spread kept: the merge carries only the 1QB mean and rank across, and the spread
    (`adp_stdev`), which drives every survival probability, would stay the 2QB one.
    """
    keys = adp_mod.feed_keys(settings)
    one = adp_mod.load_feed(*keys["1qb"])
    if adp_mode(settings) == "2qb":
        return adp_mod.merged(adp_mod.load_feed(*keys["2qb"]), one)
    one["adp_1qb"], one["adp_1qb_rank"] = one["adp"], one["adp_rank"]
    return one


MID_SEASON_BANNER = ("mid-season draft from week {w}: board on rest-of-season projections; "
                     "ADP is preseason, so survive% is rough")


def start_week(settings: dict) -> int:
    """The first scoring week the league plays (1 for a preseason draft)."""
    return settings.get("first_week") or 1


def weeks_left(settings: dict) -> int:
    """Weeks of the season the drafted roster will play."""
    from .draft_watch import SEASON_WEEKS  # draft_watch imports this module
    return SEASON_WEEKS - (start_week(settings) - 1)


def banner(settings: dict) -> str | None:
    """The mid-season warning, or None for a draft from week 1."""
    w = start_week(settings)
    return MID_SEASON_BANNER.format(w=w) if w > 1 else None


def ros_players(players: pd.DataFrame, lookup: dict[int, dict], settings: dict) -> pd.DataFrame:
    """`proj` on what is still to be played: the sources' rest-of-season consensus where a player has one,
    else the full-season figure scaled by the weeks left. proj_espn / proj_sleeper stay season totals."""
    from .draft_watch import SEASON_WEEKS
    df = players.copy()
    ros = pd.to_numeric(df.player_id.map(lambda pid: (lookup.get(int(pid)) or {}).get("ros_proj")), errors="coerce")
    scaled = df.proj * weeks_left(settings) / SEASON_WEEKS
    df["proj"] = ros.fillna(scaled).round(1)
    return df


def load_inputs(ref=None):
    """(settings, players, adp) for the league (None: the current one), from the DB: ESPN's pool for its
    formats with Sleeper's projections averaged in, and its ADP feeds."""
    ref = ref or leagues.current()
    settings = load(ref)
    players = merge_sleeper(players_mod.pool(ref), sleeper_mod.season_players(ref))
    if start_week(settings) > 1:
        from . import espn_season
        players = ros_players(players, espn_season.season_lookup(settings, start_week(settings), ref=ref), settings)
    return settings, players, load_adp(settings)


def merge_sleeper(players: pd.DataFrame, sleeper: pd.DataFrame) -> pd.DataFrame:
    """Average ESPN and Sleeper projections (both scored to league settings). D/ST matched on team."""
    df = players.copy()
    df["key"] = df["name"].map(norm_name)
    df = df.rename(columns={"proj": "proj_espn"})
    non = sleeper[sleeper.pos != "D/ST"][["key", "pos", "proj_sleeper", "adp_2qb_sleeper"]]
    df = df.merge(non, on=["key", "pos"], how="left")
    dst = sleeper[sleeper.pos == "D/ST"][["team", "proj_sleeper"]].rename(columns={"proj_sleeper": "_dst"})
    df = df.merge(dst, on="team", how="left")
    is_dst = df.pos == "D/ST"
    df.loc[is_dst, "proj_sleeper"] = df.loc[is_dst, "_dst"]
    df = df.drop(columns=["_dst"])
    # ESPN's 0-projection rows are placeholders; don't average them in
    espn = df.proj_espn.where(df.proj_espn > 0)
    df["proj"] = pd.concat([espn, df.proj_sleeper], axis=1).mean(axis=1, skipna=True).fillna(0).round(1)
    df["proj_sources"] = espn.notna().astype(int) + df.proj_sleeper.notna().astype(int)
    return df.drop(columns=["key"])


def build(settings: dict, players: pd.DataFrame, adp: pd.DataFrame, drafted_ids: set[int] | None = None) -> pd.DataFrame:
    df = players.copy()
    df["key"] = df["name"].map(norm_name)
    # D/ST names differ across sources ("Eagles D/ST" vs "Philadelphia Defense"); match on team instead
    adp_cols = ["adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank", "bye"]
    # the spread matters as much as the mean: ADP 30 with stdev 12 is a very different bet on
    # surviving eight more picks than ADP 30 with stdev 3 (draft_watch.survival uses it)
    adp_cols += [c for c in ("adp_stdev", "times_drafted") if c in adp.columns]
    adp_pl = adp[adp.pos != "D/ST"]
    df = df.merge(adp_pl[["key", "pos"] + adp_cols], on=["key", "pos"], how="left")
    adp_dst = adp[adp.pos == "D/ST"][["team"] + adp_cols]
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
    bench = bench_depth(settings)
    repl = {}
    for pos, n in counts.items():
        pool = avail[avail.pos == pos].sort_values("proj", ascending=False).proj.values
        # league-wide demand at this position, minus what's already off the board: the
        # replacement player is the last one who still gets rostered. Bench depth has to stay
        # in the count mid-draft — dropping it drove the index to 0 once a position ran hot,
        # which made replacement == best available and zeroed out every VORP at that position.
        demand = int(math.ceil(n + bench[pos]))
        idx = demand - 1 - (_taken(df, pos) if drafted_ids else 0)
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
    one_fmt = adp_mod.feed_keys(settings)["1qb"][0]
    if adp_mode(settings) == "2qb":
        market, feeds = "2QB", f"ADP = FantasyFootballCalculator 2QB drafts; ADP1 = same site's 1QB ({one_fmt}) drafts."
    else:
        market, feeds = "1QB", f"ADP = FantasyFootballCalculator 1QB ({one_fmt}) drafts (ADP1 is the same)."
    L = [f"# Draft board: {settings['name']} {settings['season']}", "",
         f"{settings['team_count']} teams | starters {settings['starters']} | bench {settings['bench_size']} | "
         f"PPR={ks['ppr']} passTD={ks['pass_td']} passYd={ks['pass_yd']} INT={ks['int']}", "",
         f"{feeds} value = ADP rank minus board rank (positive = tends to fall past his value).", ""]
    cols = ["blend_rank", "board_rank", "name", "pos", "team", "bye", "proj", "proj_espn", "proj_sleeper", "vorp", "tier", "adp", "adp_1qb", "value_vs_adp", "injury"]
    a = df[~df.drafted]
    L += [f"## Overall (blend of VORP rank and {market} ADP rank)", "", a.head(top_n)[cols].to_markdown(index=False), ""]
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
        sub = a[a.pos == pos]
        n = {"QB": 32, "RB": 45, "WR": 50, "TE": 20, "D/ST": 14, "K": 12}[pos]
        L += [f"## {pos} (replacement ≈ {sub.repl.iloc[0]:.0f} pts)" if len(sub) else f"## {pos}", "",
              sub.head(n)[["pos_rank", "blend_rank", "tier", "name", "team", "bye", "proj", "proj_espn", "proj_sleeper", "vorp", "adp", "adp_1qb", "value_vs_adp", "injury"]].to_markdown(index=False), ""]
    return "\n".join(L)


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    settings, players, adp = load_inputs(leagues.get(argv[0]) if argv else None)
    df = build(settings, players, adp)
    print(to_markdown(df, settings))


if __name__ == "__main__":
    main()
