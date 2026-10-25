"""Sync season history from ESPN into the DB (transactions and weekly results).

    uv run python -m ffl.journal sync        # transactions + weekly results
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import Callable

from espn_api.football.activity import Activity

from . import config, db, leagues, notify
from .config import league
from .settings import load


# Called with the rows a sync logged, whichever path ran it (the hourly tick, a page build, the box-score reconcile):
# the API drops its cached Transactions page here, so a move notice's link never opens the page from before the move.
ON_NEW_ROWS: list[Callable[[list[dict]], None]] = []


def _my_team(lg, settings, ref=None):
    tid = leagues.my_team_id(settings, ref)
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


def sync_transactions(lg, *, ref=None):
    from datetime import datetime
    r = ref if ref is not None else leagues.home()
    rows = []
    for act in _activities(lg, size=100):
        for team, action, player, bid in act.actions:
            rows.append({"id": f"{act.date}-{team.team_id}-{action}-{getattr(player, 'playerId', player)}",
                         "date": datetime.fromtimestamp(act.date / 1000).isoformat(timespec="minutes"),
                         "type": action, "team": team.team_name, "player": getattr(player, "name", player),
                         "action": action, "bid": bid, "reasoning": ""})
    new = db.add_transactions(rows, ref=r)  # the (season, id) key makes concurrent syncs safe
    if not r.home:  # pushes and the API's page listeners are home's until 4.8
        print(f"transactions {r.key}: +{len(new)} (no notices before 4.8)")
        return new
    notify.enqueue_moves(new)
    if new:
        for fn in ON_NEW_ROWS:
            try:
                fn(new)
            except Exception as e:  # noqa: BLE001 — the rows are logged; a listener's failure is not the sync's
                print(f"transactions: listener {getattr(fn, '__name__', fn)} failed: {e}")
    print(f"transactions: +{len(new)}")
    return new


def sync_results(lg, settings, *, ref=None):
    r = ref if ref is not None else leagues.home()
    me = _my_team(lg, settings, r)
    rows = []
    for wk in range(settings.get("first_week") or 1, lg.current_week):
        for m in lg.scoreboard(wk):
            if me in (m.home_team, m.away_team):
                mine, opp = (m.home_score, m.away_score) if m.home_team == me else (m.away_score, m.home_score)
                other = m.away_team if m.home_team == me else m.home_team
                rows.append({"week": wk, "opponent": other.team_name, "my_score": mine, "opp_score": opp,
                             "result": "W" if mine > opp else "L" if mine < opp else "T"})
    db.put_results(rows, ref=r)
    print(f"results: {len(rows)} weeks")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    ref = leagues.current()
    settings = load(ref)
    lg = league(ref)
    if cmd == "sync":
        sync_transactions(lg, ref=ref)
        sync_results(lg, settings, ref=ref)


if __name__ == "__main__":
    main()
