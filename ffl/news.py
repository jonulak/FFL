"""Per-player news for the Roster outlook: Rotowire blurbs, Sleeper's depth order, ESPN's news stamps.

The parsers are pure and tested; the fetchers cache and serve the last good data on failure, like
espn_season.fetch_injuries, and an empty value when they have never succeeded. Nothing here decides
anything: outlook.py puts it on the fact sheet.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests
from espn_api.football.constant import PRO_TEAM_MAP

from .adp import norm_name
from .config import raw_league_get

ROTOWIRE = "https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players"
SLEEPER_PLAYERS = "https://api.sleeper.app/v1/players/nfl"
# ESPN's own Story articles share the feed, so ask for more than are kept
ROTOWIRE_LIMIT = 5
ROTOWIRE_KEEP = 3
# the feed's own link (m.espn.go.com/wireless/story) redirects to ESPN's home page; the player page shows the blurb
PLAYER_PAGE = "https://www.espn.com/nfl/player/_/id/{pid}"
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
                    "url": PLAYER_PAGE.format(pid=it["playerId"]) if it.get("playerId") else None})
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


# Agreed with Jonathan: the teammates whose news moves his week, by position.
# (position, how many from the top of the chart); RB takes every other back on the chart.
TEAMMATES = {"QB": (("WR", 3), ("TE", 1), ("RB", 1)),
             "WR": (("QB", 1), ("WR", 3), ("TE", 1)),
             "TE": (("QB", 1), ("WR", 3), ("TE", 1)),
             "RB": (("QB", 1), ("RB", 99)),
             "K": (("QB", 1),)}
RECENT_DAYS = 10


def _ranked(rows: list[dict], pos: str) -> list[dict]:
    return [{**r, "rank": i} for i, r in enumerate((r for r in rows if r["pos"] == pos), 1)]


def own_depth(card: dict, chart: dict[str, list[dict]]) -> dict | None:
    """His place on his club's chart at his position, or None when Sleeper's chart does not name him."""
    same = _ranked(chart.get(card["team"]) or [], card["pos"])
    me = norm_name(card["name"])
    r = next((r for r in same if norm_name(r["name"]) == me), None)
    return None if r is None else {"slot": r["slot"], "order": r["order"], "rank": r["rank"], "of": len(same)}


def teammates(card: dict, chart: dict[str, list[dict]]) -> list[dict]:
    rows, me = chart.get(card["team"]) or [], norm_name(card["name"])
    out = []
    for pos, n in TEAMMATES.get(card["pos"], ()):
        out += [r for r in _ranked(rows, pos)[:n] if norm_name(r["name"]) != me]
    return out


def context(card: dict, chart: dict, index: dict[int, dict], injuries: dict[int, dict], rotowire: list[dict]) -> dict:
    """Everything the fact sheet says about him beyond the card: his depth, the teammates in scope with
    their injury status (ESPN's report, Sleeper's when ESPN has none) and news stamps, his Rotowire items."""
    by_name = {(norm_name(v["name"]), v["team"]): pid for pid, v in index.items()}
    mates = []
    for r in teammates(card, chart):
        pid = by_name.get((norm_name(r["name"]), card["team"]))
        inj = injuries.get(pid) or {}
        mates.append({"name": r["name"], "pos": r["pos"], "rank": r["rank"],
                      "status": inj.get("status") or r.get("injury") or "Active", "note": inj.get("note"),
                      "last_news": (index.get(pid) or {}).get("last_news")})
    return {"depth": own_depth(card, chart), "teammates": mates, "rotowire": rotowire,
            "last_news": (index.get(card["player_id"]) or {}).get("last_news")}


def _recent(published: str | None, today: date) -> bool:
    try:
        return (today - date.fromisoformat(published[:10])).days <= RECENT_DAYS
    except (TypeError, ValueError):
        return False


def has_news(card: dict, ctx: dict, today: date) -> bool:
    """Whether a bye-week card is worth an outlook: he is hurt, a teammate in scope is, or Rotowire
    has written about him in the last RECENT_DAYS."""
    if (card.get("injury") or "ACTIVE") != "ACTIVE":
        return True
    if any((m.get("status") or "Active").upper() != "ACTIVE" for m in ctx["teammates"]):
        return True
    return any(_recent(i.get("published"), today) for i in ctx["rotowire"])


def gather(cards: list[dict], week: int) -> dict[int, dict]:
    """context() for every card. Each source degrades to empty on its own; this never raises for one."""
    from .espn_season import fetch_injuries  # espn_season imports a lot; keep news importable alone

    chart, index, injuries = fetch_depth(), fetch_news_index(week), fetch_injuries()
    players = [c for c in cards if c["pos"] != "D/ST"]  # a defence has no Rotowire page
    with ThreadPoolExecutor(8) as pool:
        roto = dict(zip((c["player_id"] for c in players), pool.map(lambda c: fetch_rotowire(c["player_id"]), players)))
    return {c["player_id"]: context(c, chart, index, injuries, roto.get(c["player_id"], [])) for c in cards}
