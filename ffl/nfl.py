"""NFL scores and play-by-play off ESPN's public APIs (no cookies): the NFL tab's scoreboard and its game pages.

Pure parse_* seams over the raw JSON; the fetches (below) sit behind a small TTL cache that keeps the last good answer.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import requests

from . import season as S

log = logging.getLogger(__name__)

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
    return {"week": wk.get("number"), "season_type": (data.get("season") or {}).get("type"), "games": games,
            "byes": sorted(club(t.get("abbreviation", "")) for t in wk.get("teamsOnBye", []))}


def board(games: list[dict], mine: dict[str, int], opp: dict[str, int]) -> list[dict]:
    """The scoreboard's sections: every game in progress as one LIVE section first, then the games still to come by
    kickoff window in time order (a game with no kickoff, "Time TBD", last), then the finished ones (`final`) by
    window in time order. Inside a section: games with my starters, then with only the opponent's, then the
    rest, each by kickoff then away club. `mine`/`opp` count starters by club; each game carries its counts."""
    rows = [{**g, "mine": mine.get(g["away"]["abbr"], 0) + mine.get(g["home"]["abbr"], 0),
             "opp": opp.get(g["away"]["abbr"], 0) + opp.get(g["home"]["abbr"], 0)} for g in games]
    rank = lambda g: (0 if g["mine"] else 1 if g["opp"] else 2, g["kickoff"] or "", g["away"]["abbr"])  # noqa: E731
    out = []
    live = [g for g in rows if g["status"] == "in"]
    if live:
        out.append({"live": True, "final": False, "kickoff": None, "games": sorted(live, key=rank)})
    for final in (False, True):
        windows: dict[str, list[dict]] = {}
        for g in rows:
            if g["status"] != "in" and (g["status"] == "post") == final:
                windows.setdefault(g["kickoff"] or "", []).append(g)
        out += [{"live": False, "final": final, "kickoff": k or None, "games": sorted(v, key=rank)}
                for k, v in sorted(windows.items(), key=lambda kv: (kv[0] == "", kv[0]))]
    return out


_TEAM_REF = re.compile(r"/teams/(\d+)")
_ATHLETE_REF = re.compile(r"/athletes/(-?\d+)")
DST_KINDS = ("sack", "safety")


def _ref_id(obj, pattern: re.Pattern) -> str | None:
    ref = obj.get("$ref") if isinstance(obj, dict) else None
    m = pattern.search(ref or "")
    return m.group(1) if m else (str(obj["id"]) if isinstance(obj, dict) and obj.get("id") is not None else None)


def _play(p: dict, ids: dict[str, str]) -> dict:
    start = p.get("start") or {}
    team = _ref_id(start.get("team"), _TEAM_REF) or _ref_id(p.get("team"), _TEAM_REF)
    return {
        "id": str(p.get("id")), "quarter": (p.get("period") or {}).get("number"),
        "clock": (p.get("clock") or {}).get("displayValue"), "team": ids.get(team) if team else None,
        "down": start.get("downDistanceText") or None, "text": (p.get("text") or "").strip(),
        "kind": (p.get("type") or {}).get("text"), "scoring": bool(p.get("scoringPlay")),
        "turnover": bool(p.get("isTurnover")), "penalty": bool(p.get("isPenalty")),
        "score": {"away": _int(p.get("awayScore")), "home": _int(p.get("homeScore"))},
        "athletes": [int(a) for x in p.get("participants") or [] if (a := _ref_id(x.get("athlete"), _ATHLETE_REF))],
        "mine": [], "opp": [],
    }


def parse_drives(data: dict, ids: dict[str, str]) -> list[dict]:
    """Every drive of a game, newest first, each with its plays newest first (the core API's drives embed them,
    participants included; a drive still on the field has no result yet). `ids`: ESPN team id -> abbreviation."""
    out = []
    for d in reversed(data.get("items") or []):
        team = _ref_id(d.get("team"), _TEAM_REF)
        plays = [_play(p, ids) for p in reversed((d.get("plays") or {}).get("items") or [])]
        out.append({"id": str(d.get("id")), "team": ids.get(team) if team else None,
                    "description": d.get("description"), "result": d.get("displayResult") or None, "plays": plays})
    return out


def _dst_play(p: dict) -> bool:
    kind = (p.get("kind") or "").lower()
    return p["scoring"] or p["turnover"] or any(k in kind for k in DST_KINDS)


def mark(drives: list[dict], mine: dict[int, str], opp: dict[int, str], dst_mine: dict[str, str],
         dst_opp: dict[str, str], clubs: tuple[str, str]) -> list[dict]:
    """Each play's `mine`/`opp`: the starters of mine and the opponent's it names. A D/ST has no athlete id: it
    is in a play when its club is on defence (the club that is not the offence) and the play is a score, a
    turnover, a sack or a safety."""
    def names(p: dict, players: dict[int, str], dst: dict[str, str]) -> list[str]:
        out = [players[a] for a in p["athletes"] if a in players]
        defence = next((c for c in clubs if c != p["team"]), None) if p["team"] in clubs else None
        if defence in dst and _dst_play(p):
            out.append(dst[defence])
        return list(dict.fromkeys(out))
    return [{**d, "plays": [{**p, "mine": names(p, mine, dst_mine), "opp": names(p, opp, dst_opp)} for p in d["plays"]]}
            for d in drives]


def scoring_plays(drives: list[dict]) -> list[dict]:
    """The game's scoring plays, newest first (drives and their plays are already newest first)."""
    return [p for d in drives for p in d["plays"] if p["scoring"]]


RETRY_S = 60


class TtlCache:
    """Per-key TTL cache that fetches inline and keeps the last good answer through an outage (served with the
    time it was fetched). Standalone, not ffl.sources.Cached: that one's status feeds the 24 h "source failing"
    push, and an NFL outage must not page the phone. Inline, not api.Cache's stale-while-revalidate: a live game
    page polls every 15 s and must not read one poll behind. A cold failure raises: there is nothing to serve."""

    def __init__(self):
        self._d: dict = {}  # key -> (expires, payload, fetched_at, failed)
        self._lock = threading.Lock()

    def get(self, key, fetch: Callable[[], dict], ttl_of: Callable[[dict], float]):
        now = time.time()
        with self._lock:
            hit = self._d.get(key)
        if hit and hit[0] > now:
            return hit[1], (_hhmm(hit[2]) if hit[3] else None)
        try:
            data = fetch()
        except Exception as e:  # noqa: BLE001 — any failure falls back to the last good answer
            if hit is None:
                raise
            log.warning("nfl %s failed, serving %s: %s", key, _hhmm(hit[2]), e)
            with self._lock:
                cur = self._d.get(key)
                if cur is not hit:  # another call stored a newer entry while this one failed: keep it
                    return cur[1], (_hhmm(cur[2]) if cur[3] else None)
                self._d[key] = (now + RETRY_S, hit[1], hit[2], True)
            return hit[1], _hhmm(hit[2])
        with self._lock:
            self._d[key] = (now + ttl_of(data), data, now, False)
        return data, None

    def has(self, key) -> bool:
        with self._lock:
            return key in self._d

    def drop(self, *keys) -> None:
        with self._lock:
            for k in keys:
                self._d.pop(k, None)

    def expire(self) -> None:
        with self._lock:
            self._d = {k: (0.0, *v[1:]) for k, v in self._d.items()}

    def clear(self) -> None:
        with self._lock:
            self._d = {}


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


_BOARDS = TtlCache()
_DRIVES = TtlCache()


def _get(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    return r.json()


def _ts(iso: str | None) -> float | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else None
    except ValueError:
        return None


def scoreboard_ttl(sb: dict, now: float | None = None) -> float:
    """20 s while a game is on; otherwise until the next kickoff (+5 s), between 20 s and 10 min."""
    now = time.time() if now is None else now
    if any(g["status"] == "in" for g in sb["games"]):
        return 20
    times = [t for g in sb["games"] if g["status"] == "pre" and (t := _ts(g["kickoff"]))]
    if any(t <= now for t in times):  # ESPN lags at kickoff (or the start is delayed): poll until it flips to "in"
        return 20
    ahead = [t - now for t in times]
    return max(20, min(600, int(min(ahead)) + 5)) if ahead else 600


PREGAME_S = 15 * 60


def is_live(g: dict, now: float | None = None) -> bool:
    """The page should poll: the game is on, or kicks off within 15 minutes (a page opened before kickoff must
    flip to live even if the /live stream does not tick)."""
    if g["status"] == "in":
        return True
    t = _ts(g.get("kickoff"))
    return g["status"] == "pre" and t is not None and t - (time.time() if now is None else now) <= PREGAME_S


def drives_ttl(status: str, seen_final: bool = False) -> float:
    """15 s in play, 10 min before kickoff. A final game's first fetch also lives 10 min (ESPN's drives can lag
    the status flip by seconds); a refetch of an already-final game is settled and lives 24 h."""
    if status == "post":
        return 86400 if seen_final else 600
    return {"in": 15, "pre": 600}.get(status, 86400)


def shown_week(sb: dict, now: datetime | None = None) -> int | None:
    """The week the tab opens on: ESPN's, or the next once every game in it is played (season.ros_week, the rule the
    league pages follow). The site scoreboard holds the finished week until about Wednesday; the league rolls Tuesday."""
    if sb.get("season_type") != 2 or not sb.get("week"):
        return sb.get("week")
    kicks = [datetime.fromtimestamp(t, timezone.utc) for g in sb["games"] if (t := _ts(g.get("kickoff")))]
    return S.ros_week(sb["week"], kicks, now)


def scoreboard(week: int | None) -> tuple[dict, str | None]:
    """The week's games (the shown week when None), parsed."""
    if week is None:
        sb, stale = _BOARDS.get("current", lambda: parse_scoreboard(_get(SCOREBOARD)), scoreboard_ttl)
        wk = shown_week(sb)
        return (sb, stale) if wk == sb["week"] else scoreboard(wk)
    params = {"week": week, "seasontype": 2}
    return _BOARDS.get(week, lambda: parse_scoreboard(_get(SCOREBOARD, params)), scoreboard_ttl)


def drives(event_id: str, status: str, ids: dict[str, str]) -> tuple[list[dict], str | None]:
    """One game's drives and plays, newest first; [] before kickoff."""
    fetch = lambda: {"drives": parse_drives(_get(DRIVES.format(id=event_id), {"limit": 60}), ids)}  # noqa: E731
    # keyed by status too: a page opened before kickoff caches [] for 10 min, and the flip to "in" must refetch
    seen_final = status == "post" and _DRIVES.has((event_id, "post"))
    data, stale = _DRIVES.get((event_id, status), fetch, lambda _: drives_ttl(status, seen_final))
    if status == "post":
        _DRIVES.drop((event_id, "pre"), (event_id, "in"))
    return data["drives"], stale


def overlay(games: list[dict], live: dict[str, dict]) -> list[dict]:
    """FastCast's clock, quarter, score and possession over the scoreboard's, so the tab agrees with the Matchup
    page to the second while the socket is up. The scoreboard keeps what the feed lacks (down and distance)."""
    out = []
    for g in games:
        s = live.get(g["away"]["abbr"]) or live.get(g["home"]["abbr"])
        if not s or s.get("status") not in ("in", "post"):
            out.append(g)
            continue
        score = s.get("score") or {}
        side = lambda c: {**c, "score": score.get(c["abbr"], c.get("score"))}  # noqa: E731
        on = s["status"] == "in"
        out.append({**g, "status": s["status"], "quarter": s.get("quarter") or g.get("quarter"),
                    "clock": s.get("clock") if on else None, "away": side(g["away"]), "home": side(g["home"]),
                    "ball": s.get("ball") if on else None, "red_zone": bool(on and s.get("red_zone")),
                    "down": g.get("down") if on else None})
    return out


def expire_all() -> None:
    """POST /refresh: refetch on next read, the last good kept for an outage."""
    _BOARDS.expire()
    _DRIVES.expire()


def reset() -> None:
    """Tests only."""
    _BOARDS.clear()
    _DRIVES.clear()
