"""NFL scores and play-by-play off ESPN's public APIs (no cookies): the NFL tab's scoreboard and its game pages.

Pure parse_* seams over the raw JSON; the fetches (below) sit behind a small TTL cache that keeps the last good answer.
"""
from __future__ import annotations

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
DRIVES = "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{id}/competitions/{id}/drives"
CLUBS = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
         "LV", "LAR", "LAC", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WSH"}
ALIASES = {"WAS": "WSH", "JAC": "JAX", "LA": "LAR", "OAK": "LV", "SD": "LAC"}


def club(abbr: str) -> str:
    """ESPN's abbreviation for a club, from whatever a URL or another feed spelled."""
    a = (abbr or "").upper()
    return ALIASES.get(a, a)


def _int(raw) -> int | None:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _club(c: dict | None, pre: bool) -> dict:
    c = c or {}
    t = c.get("team") or {}
    rec = next((r.get("summary") for r in c.get("records") or [] if r.get("type") in ("total", None)), None)
    return {"abbr": club(t.get("abbreviation", "")), "id": str(t.get("id")), "name": t.get("shortDisplayName"),
            "location": t.get("location"), "record": rec, "score": None if pre else _int(c.get("score")),
            "winner": bool(c.get("winner"))}


def parse_scoreboard(data: dict) -> dict:
    """{week, games, byes} off the site scoreboard. `ball`/`down`/`red_zone` only while the game is on and
    not at the half (the situation block keeps the last snap's)."""
    games = []
    for e in data.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        st = e.get("status") or comp.get("status") or {}
        kind = st.get("type") or {}
        state = kind.get("state")
        sides = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        ids = {str((c.get("team") or {}).get("id")): club((c.get("team") or {}).get("abbreviation", ""))
               for c in comp.get("competitors", [])}
        sit = comp.get("situation") or {}
        on = state == "in" and kind.get("name") != "STATUS_HALFTIME"
        ball = ids.get(str(sit.get("possession"))) if on else None
        games.append({
            "event_id": str(e.get("id")), "kickoff": e.get("date"), "status": state, "detail": kind.get("shortDetail"),
            "quarter": st.get("period") if state != "pre" else None,
            "clock": st.get("displayClock") if state == "in" else None,
            "network": comp.get("broadcast") or None,
            "away": _club(sides.get("away"), state == "pre"), "home": _club(sides.get("home"), state == "pre"),
            "ball": ball, "red_zone": bool(ball and sit.get("isRedZone")),
            "down": sit.get("downDistanceText") if ball else None,
        })
    wk = data.get("week") or {}
    return {"week": wk.get("number"), "games": games,
            "byes": sorted(club(t.get("abbreviation", "")) for t in wk.get("teamsOnBye", []))}


def board(games: list[dict], mine: dict[str, int], opp: dict[str, int]) -> list[dict]:
    """The scoreboard's sections: every game in progress as one LIVE section first, then the rest by kickoff
    window in time order. Inside a section: games with my starters, then with only the opponent's, then the
    rest, each by kickoff then away club. `mine`/`opp` count starters by club; each game carries its counts."""
    rows = [{**g, "mine": mine.get(g["away"]["abbr"], 0) + mine.get(g["home"]["abbr"], 0),
             "opp": opp.get(g["away"]["abbr"], 0) + opp.get(g["home"]["abbr"], 0)} for g in games]
    rank = lambda g: (0 if g["mine"] else 1 if g["opp"] else 2, g["kickoff"] or "", g["away"]["abbr"])  # noqa: E731
    out = []
    live = [g for g in rows if g["status"] == "in"]
    if live:
        out.append({"live": True, "kickoff": None, "games": sorted(live, key=rank)})
    windows: dict[str, list[dict]] = {}
    for g in rows:
        if g["status"] != "in":
            windows.setdefault(g["kickoff"] or "", []).append(g)
    out += [{"live": False, "kickoff": k or None, "games": sorted(v, key=rank)} for k, v in sorted(windows.items())]
    return out
