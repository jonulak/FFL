"""The ESPN player pool with league-scored season projections, stored per position scoring format.

    uv run python -m ffl.players [league key] [--refresh]   # fetch what the league's formats lack or is stale, store, print

ESPN scores a player's season projection on the league's own scoring, so a position's rows are stored
under that position's scoring format (`espn_pools`, keyed by format and season): a second league on the
same formats reuses them (inside POOL_MAX_AGE_H), and a TE premium league fetches only TE. Ownership
(on_team_id, status) is the fetching league's, so it is never stored. `pool(ref)` puts the league's six
positions back together into the frame espn_players.csv used to hold.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pandas loads on a frame's first use: the API never builds one (#472)
    import pandas as pd
from espn_api.football.constant import PRO_TEAM_MAP

from . import db, formats, leagues
from .adp import csv_frame
from .config import SEASON, raw_league_get

POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]
SLOT_IDS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "D/ST": 16, "K": 17}
COLUMNS = ["player_id", "name", "pos", "team", "proj", "actual", "injury", "espn_adp", "espn_auction",
           "espn_rank_ppr", "espn_rank_std", "on_team_id", "status"]
# belong to the league that fetched them, so espn_pools (shared by format) never stores them
LEAGUE_COLUMNS = ("on_team_id", "status")
POOL_MAX_AGE_H = 24   # a stored position older than this is fetched again (projections move all season)


def _fetch_position(pos: str, limit_per_pos: int, season: int, ref) -> dict:
    filters = {"players": {
        "filterSlotIds": {"value": [SLOT_IDS[pos]]},
        "limit": limit_per_pos,
        "sortDraftRanks": {"sortPriority": 1, "sortAsc": True, "value": "PPR"},
        "filterStatsForTopScoringPeriodIds": {"value": 2, "additionalValue": [f"00{season}", f"10{season}"]},
    }}
    return raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filters)}, ref=ref,
                          scoringPeriodId=0)


def fetch_pool(limit_per_pos: int = 250, *, ref, positions: list[str] | None = None) -> list[dict]:
    """ESPN's players at each of `positions` (all six by default), scored by `ref`'s league for its season
    (this season), in `positions` order, then ESPN's."""
    season = ref.season if ref else SEASON
    positions = positions or POSITIONS
    rows = []
    # independent ESPN calls, in flight together; rows keep `positions` order
    with ThreadPoolExecutor(len(positions)) as pool:
        fetched = list(pool.map(lambda pos: _fetch_position(pos, limit_per_pos, season, ref), positions))
    for pos, data in zip(positions, fetched):
        for entry in data.get("players", []):
            p = entry["player"]
            proj = actual = 0.0
            for st in p.get("stats", []):
                if st.get("seasonId") == season and st.get("scoringPeriodId") == 0:
                    if st.get("statSourceId") == 1:
                        proj = st.get("appliedTotal", 0.0)
                    elif st.get("statSourceId") == 0:
                        actual = st.get("appliedTotal", 0.0)
            ranks = p.get("draftRanksByRankType", {})
            rows.append({
                "player_id": p["id"],
                "name": p["fullName"],
                "pos": pos,
                "team": PRO_TEAM_MAP.get(p.get("proTeamId"), "FA"),
                "proj": round(proj, 1),
                "actual": round(actual, 1),
                "injury": p.get("injuryStatus", ""),
                "espn_adp": round(p.get("ownership", {}).get("averageDraftPosition", 0) or 0, 1),
                "espn_auction": round(p.get("ownership", {}).get("auctionValueAverage", 0) or 0, 1),
                "espn_rank_ppr": ranks.get("PPR", {}).get("rank"),
                "espn_rank_std": ranks.get("STANDARD", {}).get("rank"),
                "on_team_id": entry.get("onTeamId", 0),
                "status": entry.get("status", ""),
            })
    return rows


def _age_h(fetched_at: str, now: datetime) -> float:
    return (now - datetime.fromisoformat(fetched_at)).total_seconds() / 3600


def store_pool(ref, refresh: bool = False) -> list[str]:
    """Fetch through the league the positions whose scoring format has no rows for its season, or rows older
    than POOL_MAX_AGE_H (every position with `refresh`), and store each under its format, ownership left out.
    Returns the positions fetched."""
    keys = formats.position_formats(ref)
    now = datetime.now().astimezone()
    fresh = set() if refresh else {k for k, at in db.espn_pool_fetched(list(keys.values()), ref.season).items()
                                   if _age_h(at, now) <= POOL_MAX_AGE_H}
    missing = [pos for pos in POSITIONS if keys[pos] not in fresh]
    if not missing:
        return []
    rows = fetch_pool(ref=ref, positions=missing)
    db.put_espn_pool({keys[pos]: [shareable(r) for r in rows if r["pos"] == pos] for pos in missing}, ref.season)
    return missing


def shareable(row: dict) -> dict:
    """A pool row as espn_pools keeps it: without the fetching league's ownership (LEAGUE_COLUMNS)."""
    return {k: v for k, v in row.items() if k not in LEAGUE_COLUMNS}


def pool(ref) -> pd.DataFrame:
    """The league's pool as espn_players.csv held it: its six positions' rows, one row per player, by
    projection. Never fetches. The sort is stable, so tied players keep position order, then ESPN's: an
    unstable sort reorders them, and the board's ranks with them. The rows are shared by every league on the
    format, so they carry no ownership: on_team_id is 0 and status "" for every player."""
    import pandas as pd
    keys = formats.position_formats(ref)
    stored = db.espn_pool_rows(list(keys.values()), ref.season)
    missing = [pos for pos in POSITIONS if keys[pos] not in stored]
    if missing:
        raise SystemExit(f"no ESPN pool stored for {ref.key} {ref.season} at {', '.join(missing)}: "
                         f"run `uv run python -m ffl.players {ref.key}`")
    df = pd.DataFrame([shareable(r) for pos in POSITIONS for r in stored[keys[pos]]], columns=COLUMNS)
    df = df.drop_duplicates("player_id").sort_values(["proj"], ascending=False, kind="mergesort")
    df = csv_frame(df)
    df["on_team_id"], df["status"] = 0, ""   # after the CSV round trip, which would read "" back as NaN
    return df


def main(argv: list[str] | None = None):
    argv = sys.argv[1:] if argv is None else argv
    keys = [a for a in argv if not a.startswith("--")]
    ref = leagues.acting(keys[0] if keys else None)
    fetched = store_pool(ref, refresh="--refresh" in argv)
    df = pool(ref)
    fmts = formats.position_formats(ref)
    at = db.espn_pool_fetched(list(fmts.values()), ref.season)
    print(f"{ref.key} {ref.season}: {len(df)} players")
    for pos in POSITIONS:
        print(f"  {pos:5} {fmts[pos]}  {at[fmts[pos]]}  {'fetched' if pos in fetched else 'reused'}")
    print(df.groupby("pos")["proj"].describe()[["count", "max", "50%"]])
    print(df.head(25)[["name", "pos", "team", "proj", "espn_adp", "espn_rank_ppr"]].to_string(index=False))


if __name__ == "__main__":
    main()
