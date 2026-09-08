"""ESPN + Sleeper I/O for the season, flattened to plain dicts for ffl.season.

Everything here touches the network except flatten_box_player / merge_weekly, which are the
pure seams the tests cover.
"""
from __future__ import annotations

import csv
import re
import time
from datetime import date

import requests

from . import season as S
from .adp import norm_name
from .config import JOURNAL, MY_TEAM_ID, SEASON, SWID, league
from .players import fetch_pool
from .settings import key_scoring, load
from .sleeper import TEAM_FIX, fetch as fetch_sleeper_season, score

SLEEPER_WEEK = "https://api.sleeper.app/projections/nfl/{season}/{week}"
SLEEPER_POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
INJURIES = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
# the feed's rows carry no athlete id, only player-card links with it in the path
INJ_ID_RE = re.compile(r"/id/(\d+)/")
CACHE_S = 1800
# espn_api reads current_week, the teams and their records once, in League.__init__. Under
# launchd this process lives for months, so a League built at boot would freeze the week, the
# standings and journal.sync_results' range forever. Every builder goes through fresh_league(),
# which rebuilds it once an hour; POST /refresh forces it immediately.
LEAGUE_TTL_S = 3600
_LEAGUE_BUILT_AT = 0.0
_SEASON_CACHE: list = []  # [fetched_at, lookup]
_SL: dict[int, tuple[float, dict]] = {}  # week -> (expires_at, sleeper projections)
_INJ: list = []  # [expires_at, data]


def refresh_league() -> None:
    """Drop the cached League so the next fresh_league() fetches ESPN again."""
    global _LEAGUE_BUILT_AT
    _LEAGUE_BUILT_AT = 0.0
    cache_clear = getattr(league, "cache_clear", None)  # None when tests stub `league`
    if cache_clear is not None:
        cache_clear()


def fresh_league():
    """config.league(), rebuilt when the cached one is older than LEAGUE_TTL_S."""
    global _LEAGUE_BUILT_AT
    now = time.time()
    if now - _LEAGUE_BUILT_AT >= LEAGUE_TTL_S:
        refresh_league()
        _LEAGUE_BUILT_AT = now
    return league()


def flatten_box_player(bp, season_proj: float | None = None) -> dict:
    proj = float(getattr(bp, "projected_points", 0) or 0)
    return {"player_id": bp.playerId, "name": bp.name, "pos": bp.position, "team": bp.proTeam,
            # BoxPlayer already defaults slot_position to "FA" (free agents come back as
            # BoxPlayers too); the getattr is belt-and-braces for a plain Player.
            "slot": getattr(bp, "slot_position", "FA"), "bye": bool(getattr(bp, "on_bye_week", False)),
            "injury": bp.injuryStatus or "ACTIVE", "proj_espn": proj, "proj_sleeper": None, "proj": proj,
            "spread": None, "espn_only": True, "actual": float(getattr(bp, "points", 0) or 0),
            "game_played": int(getattr(bp, "game_played", 0) or 0),
            "season_proj": float(season_proj if season_proj is not None else getattr(bp, "projected_total_points", 0) or 0),
            "season_proj_sleeper": None, "return_week": None, "injury_note": None}


def fetch_sleeper_week(week: int, ks: dict) -> dict[tuple[str, str], float]:
    r = requests.get(SLEEPER_WEEK.format(season=SEASON, week=week),
                     params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in SLEEPER_POS],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return parse_sleeper_rows(r.json(), ks)


def parse_sleeper_rows(rows: list[dict], ks: dict) -> dict[tuple[str, str], float]:
    """Sleeper's weekly rows keyed the way merge_weekly looks them up.

    Rows without `gp` are inactive players, whose stats are all zero — averaging that against
    ESPN would halve a real projection, so they are skipped and left `espn_only`.
    """
    out: dict[tuple[str, str], float] = {}
    for p in rows:
        pl, st = p.get("player", {}), p.get("stats", {})
        pos = pl.get("position")
        if pos not in SLEEPER_POS or not st or not st.get("gp"):
            continue
        team = TEAM_FIX.get(pl.get("team") or "", pl.get("team") or "")
        if pos == "DEF":
            out[(team, "D/ST")] = float(st.get("pts_std") or 0)
        elif pos == "K":
            out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), "K")] = float(st.get("pts_std") or 0)
        else:
            out[(norm_name(f"{pl.get('first_name','')} {pl.get('last_name','')}"), pos)] = round(score(st, ks), 1)
    return out


def merge_weekly(players: list[dict], sleeper: dict[tuple[str, str], float]) -> list[dict]:
    """Average ESPN and Sleeper weekly points, as board.merge_sleeper does for the season."""
    out = []
    for p in players:
        # ESPN's abbreviations differ from Sleeper's (WSH/WAS, JAC/JAX): normalise before matching.
        key = ((TEAM_FIX.get(p["team"], p["team"]), "D/ST") if p["pos"] == "D/ST"
               else (norm_name(p["name"]), p["pos"]))
        sl = sleeper.get(key)
        q = dict(p)
        if sl is None:
            out.append(q)
            continue
        espn = p["proj_espn"] if p["proj_espn"] > 0 else None
        q["proj_sleeper"] = sl
        q["proj"] = round((espn + sl) / 2, 1) if espn is not None else sl
        q["spread"] = round(abs(espn - sl), 1) if espn is not None else None
        q["espn_only"] = False
        out.append(q)
    return out


def my_team_id(lg) -> int:
    if MY_TEAM_ID:
        return MY_TEAM_ID
    settings = load()
    tid = next((t["id"] for t in settings["teams"] if SWID in t.get("owner_swids", [])), None)
    if tid is None:
        raise RuntimeError("cannot identify my team: set MY_TEAM_ID in .env")
    return tid


def build_side(team, players: list[dict], settings: dict) -> dict:
    """One matchup side from already-merged player dicts."""
    order = {s: i for i, s in enumerate(S.SLOT_ORDER)}
    lineup = sorted((p for p in players if p["slot"] not in S.BENCH_SLOTS), key=lambda p: order.get(p["slot"], 99))
    bench = [p for p in players if p["slot"] == "BE"]
    ir = [p for p in players if p["slot"] == "IR"]
    cur, opt = S.current_lineup(players), S.optimal_lineup(players, settings)
    return {"team_id": team.team_id, "team": team.team_name, "lineup": lineup, "bench": bench, "ir": ir,
            "optimal": opt, "optimal_swaps": S.lineup_diff(cur, opt),
            "proj_total": S.lineup_total(cur), "optimal_total": S.lineup_total(opt),
            "actual_total": round(sum(p["actual"] for p in lineup), 1)}


def merge_season(espn_rows: list[dict], sleeper) -> dict[int, dict]:
    """Season projections by ESPN player id, averaged across sources like board.merge_sleeper.

    Non-D/ST match on (normalised name, position); D/ST on team, with ESPN's abbreviation
    normalised through TEAM_FIX. An ESPN 0 is a placeholder row, not a projection.
    """
    rows = sleeper.to_dict("records") if hasattr(sleeper, "to_dict") else list(sleeper)
    by_name = {(r["key"], r["pos"]): r["proj_sleeper"] for r in rows if r["pos"] != "D/ST"}
    by_team = {r["team"]: r["proj_sleeper"] for r in rows if r["pos"] == "D/ST"}
    out: dict[int, dict] = {}
    for row in espn_rows:
        team = TEAM_FIX.get(row["team"], row["team"])
        sl = by_team.get(team) if row["pos"] == "D/ST" else by_name.get((norm_name(row["name"]), row["pos"]))
        espn = row["proj"] if (row.get("proj") or 0) > 0 else None
        have = [float(x) for x in (espn, sl) if x is not None]
        out[row["player_id"]] = {"season_proj": round(sum(have) / len(have), 1) if have else 0.0,
                                 "season_proj_sleeper": float(sl) if sl is not None else None}
    return out


def apply_season(players: list[dict], lookup: dict[int, dict]) -> list[dict]:
    """Season projections from the full-pool lookup, where it has the player.

    box_scores comes from ESPN's mMatchupScore view, which carries only weekly stats — every
    BoxPlayer's projected_total_points is 0 there — so the flattened value is only a fallback.
    """
    out = []
    for p in players:
        q = dict(p)
        got = lookup.get(p["player_id"])
        if got:
            q["season_proj"] = got["season_proj"]
            q["season_proj_sleeper"] = got["season_proj_sleeper"]
        out.append(q)
    return out


def season_lookup(settings: dict) -> dict[int, dict]:
    """The whole player pool's season projections, cached for CACHE_S (one ESPN + one Sleeper call)."""
    now = time.time()
    if _SEASON_CACHE and now - _SEASON_CACHE[0] < CACHE_S:
        return _SEASON_CACHE[1]
    lookup = merge_season(fetch_pool(), fetch_sleeper_season())
    _SEASON_CACHE[:] = [now, lookup]
    return lookup


def parse_injuries(payload: dict) -> dict[int, dict]:
    """ESPN's public injuries feed keyed by ESPN player id (= BoxPlayer.playerId).

    A row without a player-card link cannot be matched to a roster, so it is dropped. `Active`
    rows are kept: their comment is real news (a depth-chart move, a return to practice) even
    though they carry no `details` and so no return date.
    """
    out: dict[int, dict] = {}
    for team in payload.get("injuries", []):
        for row in team.get("injuries", []):
            hrefs = (link.get("href") or "" for link in row.get("athlete", {}).get("links", []))
            pid = next((int(m.group(1)) for h in hrefs if (m := INJ_ID_RE.search(h))), None)
            if pid is None:
                continue
            det = row.get("details") or {}
            rd = det.get("returnDate")
            note = (row.get("shortComment") or "").strip() or (row.get("longComment") or "").strip()
            out[pid] = {"return_date": date.fromisoformat(rd) if rd else None,
                        "note": note or None, "body_part": det.get("type")}
    return out


def fetch_injuries() -> dict[int, dict]:
    """Every team's injury rows, cached for CACHE_S; an outage serves the last good data.

    Nothing is logged on failure: season.injury_terms falls back to its per-status constants, so
    a missing return week is degraded advice, not an error.
    """
    if _INJ and _INJ[0] > time.time():
        return _INJ[1]
    try:
        r = requests.get(INJURIES, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        r.raise_for_status()
        data = parse_injuries(r.json())
    except requests.RequestException:
        return _INJ[1] if _INJ else {}
    _INJ[:] = [time.time() + CACHE_S, data]
    return data


def apply_injuries(players: list[dict], injuries: dict[int, dict], week1_start: date | None) -> list[dict]:
    """The return week and injury note from the feed, where it has the player.

    Without week1_start — a settings.json written before that key existed — a return date cannot
    be turned into a week, so only the note is applied and season.games_out stays None.
    """
    out = []
    for p in players:
        q = dict(p)
        got = injuries.get(p["player_id"])
        if got:
            q["injury_note"] = got["note"]
            rd = got["return_date"]
            q["return_week"] = S.week_of(rd, week1_start) if rd and week1_start else None
        out.append(q)
    return out


def _sleeper_for(week: int, settings: dict) -> dict:
    """Sleeper's weekly projections, cached for CACHE_S per week (POST /refresh clears _SL)."""
    hit = _SL.get(week)
    if hit and hit[0] > time.time():
        return hit[1]
    data = fetch_sleeper_week(week, key_scoring(settings["scoring"]))
    _SL[week] = (time.time() + CACHE_S, data)
    return data


def _season_for(settings: dict, season: dict | None) -> dict:
    return season if season is not None else season_lookup(settings)


def _injuries_for(injuries: dict | None) -> dict:
    return injuries if injuries is not None else fetch_injuries()


def _week1_start(settings: dict) -> date | None:
    """settings.json's week 1 Tuesday, absent from files written before ffl.settings wrote it."""
    d = settings.get("week1_start")
    return date.fromisoformat(d) if d else None


def _boxes_by_team(lg, week: int) -> dict[int, list]:
    """Every team's roster for the week, from a single box_scores call."""
    out: dict[int, list] = {}
    for m in lg.box_scores(week):
        for t, lineup in ((m.home_team, m.home_lineup), (m.away_team, m.away_lineup)):
            if hasattr(t, "team_id"):
                out[t.team_id] = lineup
    return out


def week_state(week: int | None = None, sleeper: dict | None = None, season: dict | None = None,
               injuries: dict | None = None) -> dict:
    lg = fresh_league()
    settings = load()
    week = week or lg.current_week
    me = my_team_id(lg)
    sl = sleeper if sleeper is not None else _sleeper_for(week, settings)
    seas = _season_for(settings, season)
    inj, w1 = _injuries_for(injuries), _week1_start(settings)
    for m in lg.box_scores(week):
        sides = [(m.home_team, m.home_lineup), (m.away_team, m.away_lineup)]
        if not any(getattr(t, "team_id", None) == me for t, _ in sides):
            continue
        built = {}
        for t, lineup in sides:
            if not hasattr(t, "team_id"):
                continue  # a bye or placeholder side: no opponent to build
            players = apply_injuries(
                apply_season(merge_weekly([flatten_box_player(bp) for bp in lineup], sl), seas), inj, w1)
            built["me" if t.team_id == me else "opp"] = build_side(t, players, settings)
        return {"week": week, "live": S.is_live(), "me": built["me"], "opp": built.get("opp")}
    raise RuntimeError(f"no matchup for team {me} in week {week}")


def free_agent_pool(week: int | None = None, size: int = 60, sleeper: dict | None = None,
                    season: dict | None = None, injuries: dict | None = None) -> dict:
    lg = fresh_league()
    settings = load()
    week = week or lg.current_week
    me = my_team_id(lg)
    sl = sleeper if sleeper is not None else _sleeper_for(week, settings)
    seas = _season_for(settings, season)
    inj, w1 = _injuries_for(injuries), _week1_start(settings)
    boxes = _boxes_by_team(lg, week)
    if me not in boxes:
        raise RuntimeError(f"no box score for team {me} in week {week}")
    my_players = apply_injuries(
        apply_season(merge_weekly([flatten_box_player(bp) for bp in boxes[me]], sl), seas), inj, w1)
    rostered = apply_injuries(
        apply_season([flatten_box_player(bp) for lineup in boxes.values() for bp in lineup], seas), inj, w1)
    out = {}
    for pos in ["QB", "RB", "WR", "TE", "D/ST", "K"]:
        box = [flatten_box_player(bp) for bp in lg.free_agents(week=week, size=size, position=pos)]
        frees = apply_injuries(apply_season(merge_weekly(box, sl), seas), inj, w1)
        for f in frees:
            f["slot"] = "FA"
        out[pos] = S.waiver_deltas(frees, my_players, rostered + frees, settings, week)
    return {"week": week, "positions": out}


def league_state() -> dict:
    lg = fresh_league()
    me = my_team_id(lg)
    wk = lg.current_week
    standings = [{"rank": i, "team_id": t.team_id, "team": t.team_name, "wins": t.wins, "losses": t.losses,
                  "ties": getattr(t, "ties", 0), "points_for": round(t.points_for, 1),
                  "points_against": round(t.points_against, 1), "me": t.team_id == me}
                 for i, t in enumerate(lg.standings(), 1)]
    board = [{"home": m.home_team.team_name, "away": m.away_team.team_name,
              "home_score": round(m.home_score, 1), "away_score": round(m.away_score, 1)} for m in lg.scoreboard(wk)]
    power = [{"team": t.team_name, "score": float(s)} for s, t in lg.power_rankings(wk)]
    return {"week": wk, "standings": standings, "scoreboard": board, "power": power}


def journal_state() -> dict:
    from .journal import TX_CSV
    rows = []
    if TX_CSV.exists():
        with TX_CSV.open() as f:
            rows = list(csv.DictReader(f))
    return {"decisions_md": (JOURNAL / "DECISIONS.md").read_text(), "transactions": rows}


def draft_recap() -> dict:
    picks_csv = JOURNAL / "draft" / "picks.csv"
    recap = JOURNAL / "draft" / "recap.md"
    picks = []
    if picks_csv.exists():
        with picks_csv.open() as f:
            picks = list(csv.DictReader(f))
    return {"picks": picks, "recap_md": recap.read_text() if recap.exists() else ""}
