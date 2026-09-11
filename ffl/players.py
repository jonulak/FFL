"""Fetch the ESPN player pool with league-scored season projections -> data/espn_players.csv."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from espn_api.football.constant import POSITION_MAP, PRO_TEAM_MAP

from .config import DATA, SEASON, raw_league_get

POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]
SLOT_IDS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "D/ST": 16, "K": 17}


def _fetch_position(pos: str, limit_per_pos: int) -> dict:
    filters = {"players": {
        "filterSlotIds": {"value": [SLOT_IDS[pos]]},
        "limit": limit_per_pos,
        "sortDraftRanks": {"sortPriority": 1, "sortAsc": True, "value": "PPR"},
        "filterStatsForTopScoringPeriodIds": {"value": 2, "additionalValue": [f"00{SEASON}", f"10{SEASON}"]},
    }}
    return raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filters)}, scoringPeriodId=0)


def fetch_pool(limit_per_pos: int = 250) -> list[dict]:
    rows = []
    # six independent ESPN calls, in flight together; rows keep POSITIONS order
    with ThreadPoolExecutor(len(POSITIONS)) as pool:
        fetched = list(pool.map(lambda pos: _fetch_position(pos, limit_per_pos), POSITIONS))
    for pos, data in zip(POSITIONS, fetched):
        for entry in data.get("players", []):
            p = entry["player"]
            proj = actual = 0.0
            for st in p.get("stats", []):
                if st.get("seasonId") == SEASON and st.get("scoringPeriodId") == 0:
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


def main():
    df = pd.DataFrame(fetch_pool())
    df = df.drop_duplicates("player_id").sort_values(["proj"], ascending=False)
    df.to_csv(DATA / "espn_players.csv", index=False)
    print(f"{len(df)} players -> data/espn_players.csv")
    print(df.groupby("pos")["proj"].describe()[["count", "max", "50%"]])
    print(df.head(25)[["name", "pos", "team", "proj", "espn_adp", "espn_rank_ppr"]].to_string(index=False))


if __name__ == "__main__":
    main()
