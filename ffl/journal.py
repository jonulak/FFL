"""Sync season history from ESPN into journal/ (git-tracked).

    uv run python -m ffl.journal sync        # transactions + weekly results + standings
"""
from __future__ import annotations

import csv
import json
import sys
import threading
from types import SimpleNamespace

from espn_api.football.activity import Activity

from .config import JOURNAL, MY_TEAM_ID, SWID, league
from .settings import load

TX_CSV = JOURNAL / "transactions.csv"
WEEKLY_CSV = JOURNAL / "results" / "weekly.csv"
STANDINGS_MD = JOURNAL / "results" / "standings.md"


def _my_team(lg, settings):
    tid = MY_TEAM_ID or next((t["id"] for t in settings["teams"] if SWID in t.get("owner_swids", [])), None)
    return next(t for t in lg.teams if t.team_id == tid)


def _activities(lg, size: int) -> list[Activity]:
    """League.recent_activity, with dropped players named from the league's player map.

    espn-api resolves any player who is on nobody's roster (every DROPPED row) with
    `player_info`, two ESPN requests each; two dozen drops is fourteen seconds, past the UI's
    20 s abort on POST /refresh. The map already holds the whole pool, and the row key only
    needs `playerId`, so nothing here touches the network beyond the one activity fetch.
    """
    msg_types = [178, 180, 179, 239, 181, 244]
    filters = {"topics": {"filterType": {"value": ["ACTIVITY_TRANSACTIONS"]}, "limit": size,
                          "limitPerMessageSet": {"value": 25}, "offset": 0,
                          "sortMessageDate": {"sortPriority": 1, "sortAsc": False},
                          "sortFor": {"sortPriority": 2, "sortAsc": False},
                          "filterIncludeMessageTypeIds": {"value": msg_types}}}
    data = lg.espn_request.league_get(extend="/communication/", params={"view": "kona_league_communication"},
                                      headers={"x-fantasy-filter": json.dumps(filters)})

    def local_player(playerId):
        return SimpleNamespace(playerId=playerId, name=lg.player_map.get(playerId, playerId))

    return [Activity(topic, lg.player_map, lg.get_team_data, local_player) for topic in data["topics"]]


def sync_transactions(lg):
    # Refresh and every Transactions page build both sync; two appends reading the same `existing`
    # would write each new row twice.
    with _TX_LOCK:
        _sync_transactions(lg)


_TX_LOCK = threading.Lock()


def _sync_transactions(lg):
    existing = set()
    if TX_CSV.exists():
        with TX_CSV.open() as f:
            existing = {r["id"] for r in csv.DictReader(f)}
    # header on a missing/empty file only — an existing header-only file must not get a second one
    write_header = not TX_CSV.exists() or TX_CSV.stat().st_size == 0
    new = 0
    with TX_CSV.open("a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["id", "date", "type", "team", "player", "action", "bid", "reasoning"])
        for act in _activities(lg, size=100):
            for team, action, player, bid in act.actions:
                key = f"{act.date}-{team.team_id}-{action}-{getattr(player, 'playerId', player)}"
                if key in existing:
                    continue
                from datetime import datetime
                w.writerow([key, datetime.fromtimestamp(act.date / 1000).isoformat(timespec="minutes"), action, team.team_name,
                            getattr(player, "name", player), action, bid, ""])
                new += 1
    print(f"transactions: +{new}")


def sync_results(lg, settings):
    me = _my_team(lg, settings)
    rows = []
    for wk in range(1, lg.current_week):
        for m in lg.scoreboard(wk):
            if me in (m.home_team, m.away_team):
                mine, opp = (m.home_score, m.away_score) if m.home_team == me else (m.away_score, m.home_score)
                other = m.away_team if m.home_team == me else m.home_team
                rows.append([wk, other.team_name, mine, opp, "W" if mine > opp else "L" if mine < opp else "T"])
    WEEKLY_CSV.parent.mkdir(parents=True, exist_ok=True)  # /refresh calls this without journal.main
    with WEEKLY_CSV.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["week", "opponent", "my_score", "opp_score", "result"])
        w.writerows(rows)
    lines = [f"# Standings (through week {lg.current_week - 1})", "", "| # | Team | W-L | PF | PA |", "|---|---|---|---|---|"]
    for i, t in enumerate(lg.standings(), 1):
        lines.append(f"| {i} | {t.team_name}{' (me)' if t == me else ''} | {t.wins}-{t.losses} | {t.points_for:.1f} | {t.points_against:.1f} |")
    STANDINGS_MD.write_text("\n".join(lines) + "\n")
    print(f"results: {len(rows)} weeks; standings written")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    settings = load()
    lg = league()
    if cmd == "sync":
        sync_transactions(lg)
        sync_results(lg, settings)


if __name__ == "__main__":
    main()
