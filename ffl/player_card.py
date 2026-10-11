"""One player's game log, this season and last, from ESPN's kona_playercard view: every week's raw
stats scored with the league's table (live.box_score, so a week reads exactly like the Matchup
breakdown), the week's opponent off that season's pro schedule, and the season's totals. This
season's games still to play follow the log with their projections, and every game this season,
played or ahead, carries the opponent's rank against his position (ESPN's positionAgainstOpponent).
His own rank at the position rides on this season's totals and each finished week (parse_pos_ranks).
Pure: the fetch is espn_season.player_card's."""
from __future__ import annotations

import time

from espn_api.football.constant import PRO_TEAM_MAP

from . import consensus, live
from .adp import norm_name
from .espn_season import parse_bye_by_team as parse_byes  # the same document shape, any season
from .sleeper import TEAM_FIX

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}

# The collapsed row: the position's own stat group, fixed columns so the weeks read straight down.
# (label, made id, attempts id) for a made/attempted pair, (label, id) for a count.
SUMMARY = {
    "QB": [("C/A", "1", "0"), ("YDS", "3"), ("TD", "4"), ("INT", "20")],
    "RB": [("CAR", "23"), ("YDS", "24"), ("TD", "25")],
    "WR": [("REC/TGT", "53", "58"), ("YDS", "42"), ("TD", "43")],
    "TE": [("REC/TGT", "53", "58"), ("YDS", "42"), ("TD", "43")],
    "K": [("FG", "83", "84"), ("XP", "86", "87")],
    "D/ST": [("SCK", "99"), ("INT", "95"), ("PA", "120")],
}


def summary(pos: str, stats: dict) -> list[list[str]]:
    def v(sid: str) -> str:
        return live._n(float(stats.get(sid) or 0))
    return [[c[0], f"{v(c[1])}/{v(c[2])}" if len(c) == 3 else v(c[1])] for c in SUMMARY.get(pos, [])]


def parse_games(sched: dict) -> dict[int, tuple[int, str, str]]:
    """Every game in a proTeamSchedules_wl document: ESPN game id -> (week, away, home), the clubs
    as PRO_TEAM_MAP abbreviates them (the document's own `abbrev` is mixed case)."""
    out = {}
    for t in sched.get("settings", {}).get("proTeams", []):
        for wk, games in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in games:
                out[int(g["id"])] = (int(wk), PRO_TEAM_MAP.get(g["awayProTeamId"]), PRO_TEAM_MAP.get(g["homeProTeamId"]))
    return out


GAMES_PLAYED = "210"


def _game_rows(rows: list[dict], season: int) -> list[dict]:
    """The games he played: the season's actual per-week rows (statSourceId 0 actual,
    statSplitTypeId 1 one week) that carry stat 210, games played. ESPN also writes an empty row
    for a game he sat out (Bryce Young, 2025 week 8); that week reads as missed, like no row."""
    return sorted((r for r in rows if r.get("seasonId") == season and r.get("statSourceId") == 0
                   and r.get("statSplitTypeId") == 1 and r.get("scoringPeriodId")
                   and (r.get("stats") or {}).get(GAMES_PLAYED)), key=lambda r: r["scoringPeriodId"])


def _game(row: dict, games: dict) -> tuple[int, str, str] | None:
    gid = str(row.get("externalId", ""))
    return games.get(int(gid)) if gid.isdigit() else None


def _clubs(played: list[dict], games: dict, fallback: str | None) -> list[str | None]:
    """His club in each game: the one his next game shares (a traded player's first game for the
    new club shares the new club with the game after it, not the old one), the previous game's for
    his last. Still ambiguous, his current club if it played; else unknown."""
    teams = [set((_game(r, games) or (0, None, None))[1:]) - {None} for r in played]
    out = []
    for i, t in enumerate(teams):
        club = None
        for j in (i + 1, i - 1):
            if 0 <= j < len(teams) and len(t & teams[j]) == 1:
                club = next(iter(t & teams[j]))
                break
        if club is None and fallback in t:
            club = fallback
        out.append(club)
    return out


def _entry(week: int, row: dict, club: str | None, game: tuple | None, pos: str, scoring: list[dict],
           status: str = "played") -> dict:
    stats = row.get("stats") or {}
    box = live.box_score(stats, scoring)
    away, home = (game[1], game[2]) if game else (None, None)
    opp = (home if club == away else away) if club and club in (away, home) else None
    return {"week": week, "opp": opp, "home": (club == home) if opp else None, "status": status,
            "pts": round(sum(b["points"] for b in box), 2), "summary": summary(pos, stats), "box": box,
            "volume": live.volume(pos, stats)}


def _blank(week: int, status: str) -> dict:
    return {"week": week, "opp": None, "home": None, "status": status, "pts": None, "summary": [], "box": [], "volume": None}


def season_log(rows: list[dict], season: int, pos: str, scoring: list[dict], games: dict, byes: dict,
               through: int, fallback_club: str | None) -> dict | None:
    """Weeks 1..`through` of one season, then its totals; None when he played no game in it.
    A week without a row is his club's bye, or a game he missed; the `through` week itself shows
    only once he has played it (or it is his bye), since before kickoff there is nothing to show."""
    played = _game_rows(rows, season)
    if not played:
        return None
    by_week = {r["scoringPeriodId"]: (r, c) for r, c in zip(played, _clubs(played, games, fallback_club))}
    weeks = []
    for wk in range(1, through + 1):
        if wk in by_week:
            r, club = by_week[wk]
            weeks.append(_entry(wk, r, club, _game(r, games), pos, scoring))
            continue
        # his club either side of the gap: a bye in the week he changed clubs is either one's
        near = [c for _, (_, c) in sorted(by_week.items(), key=lambda kv: abs(kv[0] - wk))[:2]] or [fallback_club]
        if any(c and byes.get(c) == wk for c in near):
            weeks.append(_blank(wk, "bye"))
        elif wk < through:
            weeks.append(_blank(wk, "out"))
    totals: dict[str, float] = {}
    for r in played:
        for k, n in (r.get("stats") or {}).items():
            totals[k] = totals.get(k, 0.0) + float(n)
    box = live.box_score(totals, scoring)
    for b in box:  # a band's season count is the games that landed in it, not its summed basis
        if b["flat"]:
            b["count"] = totals.get(str(b["stat_id"]), 0.0)
    pts = round(sum(w["pts"] for w in weeks if w["status"] == "played"), 2)
    return {"season": season, "weeks": weeks,
            "totals": {"games": len(played), "pts": pts, "ppg": round(pts / len(played), 1),
                       "summary": summary(pos, totals), "box": box}}


def parse_ranks(doc: dict) -> dict[str, dict[str, dict]]:
    """ESPN's opponent ranks from a kona_player_info response's positionAgainstOpponent: position ->
    club -> {rank, of, allowed}. `allowed` is the fantasy points the position has scored per game
    against that defence this season; rank 1 allowed the fewest, so the last rank is the softest."""
    out: dict[str, dict[str, dict]] = {}
    for pid, r in ((doc.get("positionAgainstOpponent") or {}).get("positionalRatings") or {}).items():
        pos, opps = POS.get(int(pid)), r.get("ratingsByOpponent") or {}
        if pos:
            out[pos] = {PRO_TEAM_MAP[int(t)]: {"rank": int(o["rank"]), "of": len(opps), "allowed": round(float(o["average"]), 1)}
                        for t, o in opps.items() if int(t) in PRO_TEAM_MAP and o.get("rank")}
    return out


def parse_pos_ranks(doc: dict, pos: str, season: int) -> dict:
    """Every player's rank at `pos` from a kona_player_info list of the position (espn_season.
    position_ranks): {"season": {player id: rank}, "weeks": {week: {player id: rank}}}, by ESPN's
    league-scored points (appliedTotal), the season's to date and each week's. Only a game he
    played counts (stat 210, as _game_rows); a tie shares the higher rank, as ESPN's own does."""
    totals: dict[int, float] = {}
    weeks: dict[int, dict[int, float]] = {}
    for e in doc.get("players") or []:
        p = e.get("player") or {}
        if POS.get(p.get("defaultPositionId")) != pos:
            continue
        for r in _game_rows(p.get("stats") or [], season):
            pts = float(r.get("appliedTotal") or 0)
            weeks.setdefault(r["scoringPeriodId"], {})[p["id"]] = pts
            totals[p["id"]] = totals.get(p["id"], 0.0) + pts

    def rank(by: dict[int, float]) -> dict[int, int]:
        first: dict[float, int] = {}
        for i, v in enumerate(sorted(by.values(), reverse=True), 1):
            first.setdefault(v, i)
        return {pid: first[v] for pid, v in by.items()}
    return {"season": rank(totals), "weeks": {wk: rank(by) for wk, by in weeks.items()}}


# A game is still to play until four hours after its kickoff (season.ros_week's rule). Past that,
# a week with no row is one ESPN has not written yet, and stays out of the log as it always has.
GAME_S = 4 * 3600


def week_projections(p: dict, pos: str, team: str | None, season: int, week: int,
                     sleeper: dict[int, dict[tuple[str, str], float]],
                     current: dict[str, dict] | None = None) -> dict[int, dict]:
    """His projection for each week ahead. This week: the consensus of ESPN (its statSourceId 1
    one-week row, league-scored, a 0 being a placeholder) and every weekly source (`current`,
    espn_season.weekly_sources); later weeks: Sleeper's alone (`sleeper`: week -> its rows), the one
    source that projects every week."""
    espn = next((r.get("appliedTotal") for r in p.get("stats") or []
                 if r.get("seasonId") == season and r.get("statSourceId") == 1 and r.get("statSplitTypeId") == 1
                 and r.get("scoringPeriodId") == week), None) or None
    key = (TEAM_FIX.get(team or "", team or ""), "D/ST") if pos == "D/ST" else (norm_name(p.get("fullName") or ""), pos)
    out = {}
    for wk in sorted(set(sleeper) | ({week} if espn or current else set())):
        if wk == week:
            vals = {n: float(d[key]) for n, d in (current or {"sleeper": sleeper.get(wk) or {}}).items()
                    if d.get(key) is not None}
            if espn:
                vals["espn"] = float(espn)
            proj = consensus.combine(vals, consensus.weights_for("week", pos))[0]
        else:
            sl = (sleeper.get(wk) or {}).get(key)
            proj = round(float(sl), 1) if sl is not None else None
        out[wk] = {"proj": proj}
    return out


NO_PROJ = {"proj": None}


def schedule_ahead(sched: dict, club: str | None, after: int, ranks: dict[str, dict], now: float,
                   proj: dict[int, dict] | None = None) -> list[dict]:
    """His club's weeks after `after` through the season's last: a bye, or a game still to play
    with the opponent's rank against his position (None where ESPN has none for that club) and his
    projection for it (week_projections; all None where no source has him)."""
    games: dict[int, tuple[str, bool, float]] = {}
    for t in sched.get("settings", {}).get("proTeams", []):
        for wk, gs in (t.get("proGamesByScoringPeriod") or {}).items():
            for g in gs:
                away, home = PRO_TEAM_MAP.get(g["awayProTeamId"]), PRO_TEAM_MAP.get(g["homeProTeamId"])
                if club and club in (away, home):
                    games[int(wk)] = (home if club == away else away, club == home, (g.get("date") or 0) / 1000)
    if not games:
        return []
    bye = parse_byes(sched).get(club)
    out = []
    for wk in range(after + 1, max(games) + 1):
        if wk == bye:
            out.append(_blank(wk, "bye"))
        elif wk in games and games[wk][2] + GAME_S > now:
            opp, home, _ = games[wk]
            out.append({**_blank(wk, "upcoming"), "opp": opp, "home": home, "opp_rank": ranks.get(opp),
                        **(proj or {}).get(wk, NO_PROJ)})
    return out


def _ranked(w: dict, by_opp: dict, by_week: dict, pid: int, current_week: int) -> dict:
    if w["opp"]:
        w = {**w, "opp_rank": by_opp.get(w["opp"])}
    if w["status"] == "played" and w["week"] < current_week:
        w = {**w, "pos_rank": by_week.get(w["week"], {}).get(pid)}
    return w


def _pct(off: float, team: float) -> int:
    return round(100 * off / team)


def with_snaps(s: dict, snaps: dict[int, tuple[float, float]]) -> dict:
    """This season's games played (or in play) with his share of the offensive snaps, None for a
    game Sleeper has no snaps for yet (they land a day after it), and the season's totals with
    the share over every game that has them (`snap_games` of them)."""
    weeks = [{**w, "snap_pct": _pct(*snaps[w["week"]]) if w["week"] in snaps else None}
             if w["status"] in ("played", "live") else w for w in s["weeks"]]
    had = [snaps[w["week"]] for w in weeks if w.get("snap_pct") is not None]
    totals = s["totals"] and {**s["totals"], "snap_games": len(had),
                              "snap_pct": _pct(sum(o for o, _ in had), sum(t for _, t in had)) if had else None}
    return {**s, "weeks": weeks, "totals": totals}


def player_log(card: dict, scoring: list[dict], schedules: dict[int, dict], current_week: int, season: int,
               history: int = 1, ranks: dict[str, dict[str, dict]] | None = None, now: float | None = None,
               sleeper: dict[int, dict[tuple[str, str], float]] | None = None, pos_ranks: dict | None = None,
               snaps: dict[int, tuple[float, float]] | None = None, current: dict | None = None) -> dict:
    """The modal's payload from a kona_playercard response: this season through the current
    week and its games still to play (schedule_ahead), then `history` past seasons whole, newest
    first; a season with neither a game played nor one ahead is left out. The rows may come from
    several calls (earlier seasons are fetched apart): each season reads only its own.
    This season's games played carry the opponent's rank too: it is this season's to date, so
    it reads how that defence has held up since, not what it was the day he played it. Past
    seasons have no ranks (ESPN keeps only the current season's). `pos_ranks` (parse_pos_ranks, his
    position's) ranks him at it: this season's totals, and each week played before the current one
    (a week still being played has no settled rank). `snaps` (week -> his offensive snaps, his
    team's) gives this season's games and totals his snap share (with_snaps)."""
    p = card["players"][0]["player"]
    pos, team = POS.get(p.get("defaultPositionId"), "?"), PRO_TEAM_MAP.get(p.get("proTeamId"))
    seasons = []
    for yr in range(season, season - history - 1, -1):
        sched = schedules.get(yr) or {}
        games = parse_games(sched)
        through = current_week if yr == season else max((w for w, _, _ in games.values()), default=18)
        s = season_log(p.get("stats") or [], yr, pos, scoring, games, parse_byes(sched), through, team)
        if yr == season:
            by_opp = (ranks or {}).get(pos, {})
            if s:
                at = pos_ranks or {}
                s = {**s, "weeks": [_ranked(w, by_opp, at.get("weeks", {}), p["id"], current_week) for w in s["weeks"]],
                     "totals": {**s["totals"], "pos_rank": at.get("season", {}).get(p["id"])}}
                s = with_snaps(s, snaps or {})
            # the current week is in the log once played (or his bye); otherwise it is still ahead
            logged = max((w["week"] for w in s["weeks"]), default=0) if s else 0
            ahead = schedule_ahead(sched, team, max(logged, current_week - 1), by_opp,
                                   time.time() if now is None else now,
                                   week_projections(p, pos, team, yr, current_week, sleeper or {}, current))
            if ahead:
                s = {**s, "weeks": s["weeks"] + ahead} if s else {"season": yr, "weeks": ahead, "totals": None}
        if s:
            seasons.append(s)
    return {"player_id": p["id"], "name": p.get("fullName"), "pos": pos, "team": team,
            "jersey": p.get("jersey") or None,  # a string ("41"); a D/ST has none
            "season": season, "current_week": current_week, "seasons": seasons}


def with_live(log: dict, week: int, stats: dict, scoring: list[dict], game: dict | None) -> dict:
    """The live week from the FastCast stat row in place of ESPN's, which runs minutes behind it.
    The opponent stays ESPN's when it has the week; a week it does not have yet goes without one
    (the header's game line carries it). A player yet to play this season gets it opened for him.
    Returns a new payload: the cached one is not touched."""
    season = log.get("season")
    first = log["seasons"][0] if log["seasons"] else None
    if first and first["season"] == season:
        cur, rest = dict(first), log["seasons"][1:]
    else:
        cur, rest = {"season": season, "weeks": [], "totals": None}, log["seasons"]
    old = next((w for w in cur["weeks"] if w["week"] == week), None)
    entry = _entry(week, {"stats": stats}, None, None, log.get("pos", ""), scoring,
                   status="live" if (game or {}).get("status") == "in" else "played")
    if old:
        entry["opp"], entry["home"] = old["opp"], old["home"]
        for k in ("opp_rank", "pos_rank", "snap_pct"):
            if k in old:
                entry[k] = old[k]
    cur["weeks"] = sorted([w for w in cur["weeks"] if w["week"] != week] + [entry], key=lambda w: w["week"])
    return {**log, "seasons": [cur, *rest]}
