"""Per-player news for the Roster outlook: Rotowire blurbs, Sleeper's depth order, ESPN's news stamps.

The parsers are pure and tested; the fetchers cache and serve the last good data on failure, like
espn_season.fetch_injuries, and an empty value when they have never succeeded. Nothing here decides
anything: outlook.py puts it on the fact sheet.
"""
from __future__ import annotations

import json
import time

import requests
from espn_api.football.constant import PRO_TEAM_MAP

from .config import raw_league_get

ROTOWIRE = "https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players"
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
# ESPN's own Story articles share the feed, so ask for more than are kept
ROTOWIRE_LIMIT = 5
ROTOWIRE_KEEP = 3
NEWS_S = 1800
DEPTH_S = 6 * 3600
SLEEPER_TO_ESPN = {"WAS": "WSH"}  # Sleeper's club codes where espn-api's differ
DEFAULT_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K"}
OFFENSE = {"QB", "RB", "WR", "TE", "K"}
_ROTO: dict[int, tuple[float, list]] = {}  # espn id -> (expires_at, items)
_DEPTH: list = []  # [expires_at, chart]
_INDEX: list = []  # [expires_at, index]


def parse_rotowire(payload: dict) -> list[dict]:
    """ESPN's fantasy news feed for one player, Rotowire blurbs only, newest first."""
    out = []
    for it in payload.get("feed") or []:
        if it.get("type") != "Rotowire":
            continue
        out.append({"id": str(it.get("id")), "headline": (it.get("headline") or "").strip(),
                    "story": (it.get("story") or "").strip(), "published": it.get("published"),
                    "url": ((it.get("links") or {}).get("mobile") or {}).get("href")})
    out.sort(key=lambda i: i["published"] or "", reverse=True)
    return out[:ROTOWIRE_KEEP]


def fetch_rotowire(pid: int) -> list[dict]:
    hit = _ROTO.get(pid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        # requests' default User-Agent: site.api.espn.com answers 403 to a browser-shaped one
        r = requests.get(ROTOWIRE, params={"playerId": pid, "limit": ROTOWIRE_LIMIT}, timeout=20)
        r.raise_for_status()
        items = parse_rotowire(r.json())
    except (requests.RequestException, ValueError):
        return hit[1] if hit else []
    _ROTO[pid] = (time.time() + NEWS_S, items)
    return items


def parse_depth(players: dict) -> dict[str, list[dict]]:
    """Sleeper's player dump as each club's offensive depth chart. Sleeper moves a hurt player down
    the order, which ESPN's own chart does not. Players without an order are not on the chart."""
    out: dict[str, list[dict]] = {}
    for v in players.values():
        team, pos, order = v.get("team"), v.get("position"), v.get("depth_chart_order")
        if not team or pos not in OFFENSE or order is None:
            continue
        out.setdefault(SLEEPER_TO_ESPN.get(team, team), []).append(
            {"name": v.get("full_name") or "", "pos": pos, "slot": v.get("depth_chart_position"),
             "order": int(order), "injury": v.get("injury_status")})
    for rows in out.values():
        rows.sort(key=lambda r: (r["pos"], r["order"], r["slot"] or ""))
    return out


def fetch_depth() -> dict[str, list[dict]]:
    if _DEPTH and _DEPTH[0] > time.time():
        return _DEPTH[1]
    try:
        r = requests.get(SLEEPER_PLAYERS, timeout=60)
        r.raise_for_status()
        chart = parse_depth(r.json())
    except (requests.RequestException, ValueError):
        return _DEPTH[1] if _DEPTH else {}
    _DEPTH[:] = [time.time() + DEPTH_S, chart]
    return chart


def parse_news_index(payload: dict) -> dict[int, dict]:
    """Every offensive player ESPN lists, with the time of his latest news item (epoch ms)."""
    out: dict[int, dict] = {}
    for e in payload.get("players") or []:
        p = e.get("player") or {}
        pos = DEFAULT_POS.get(p.get("defaultPositionId"))
        if pos is None or p.get("id") is None:
            continue
        team = PRO_TEAM_MAP.get(p.get("proTeamId"))
        out[int(p["id"])] = {"name": p.get("fullName") or "", "pos": pos,
                             "team": team if team and team != "None" else None,
                             "last_news": p.get("lastNewsDate")}
    return out


def fetch_news_index(week: int) -> dict[int, dict]:
    if _INDEX and _INDEX[0] > time.time():
        return _INDEX[1]
    filters = {"players": {"filterSlotIds": {"value": [0, 2, 4, 6, 17]}, "limit": 1500,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    try:
        data = raw_league_get("kona_player_info", headers={"x-fantasy-filter": json.dumps(filters)},
                              scoringPeriodId=week)
        index = parse_news_index(data)
    except Exception:  # noqa: BLE001 — espn-api raises its own error types; a stamp is not worth a failed run
        return _INDEX[1] if _INDEX else {}
    _INDEX[:] = [time.time() + NEWS_S, index]
    return index
