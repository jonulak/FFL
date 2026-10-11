"""Who is beating his projection, league-wide: every rostered player and the free-agent pool, ranked by
pace over the model's rest-of-season number (STRATEGY.md "Recent form": `pace_gap`), with the columns
the write-up reads beside it — where he sits (owner and slot), snap share, targets, years in, hurt
teammates ahead, and how stingy his two opponents were. The Tuesday scan for snipes and trade targets;
the write-up rules are in `.claude/skills/pace-report/SKILL.md`.

    uv run python -m ffl.pace                 # top 14 per position, two-game windows only
    uv run python -m ffl.pace --top 20 --min-games 1 --json data/pace.json

Game rows come from each form week's box scores, not the team rosters (ffl-sba: the roster path
carries only the current week). Opponent strength is points allowed over the played weeks, off ESPN's
public scoreboard, 1 = stingiest; a two-game rank is a hint, not a fact.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import espn_season as E, season as S
from .config import SEASON

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={season}&seasontype=2&week={week}"
POSITIONS = ["QB", "RB", "WR", "TE"]
POOL_SIZE = 80


def parse_scoreboard(payloads: dict[int, dict]) -> tuple[dict[str, int], dict[str, dict[int, str]]]:
    """(defensive rank by pro team, 1 = fewest points allowed a game; opponent by team and week)
    off ESPN's scoreboard documents for the played weeks. Unfinished games are left out."""
    allowed: dict[str, list[int]] = defaultdict(list)
    sched: dict[str, dict[int, str]] = defaultdict(dict)
    for week, doc in payloads.items():
        for ev in doc.get("events", []):
            comp = ev["competitions"][0]
            if comp["status"]["type"]["state"] != "post":
                continue
            side = {c["homeAway"]: (c["team"]["abbreviation"], int(c["score"])) for c in comp["competitors"]}
            (h, hs), (a, as_) = side["home"], side["away"]
            allowed[h].append(as_), allowed[a].append(hs)
            sched[h][week], sched[a][week] = a, h
    order = sorted(allowed, key=lambda t: sum(allowed[t]) / len(allowed[t]))
    return {t: i + 1 for i, t in enumerate(order)}, dict(sched)


def fetch_scoreboards(season: int, weeks: list[int]) -> dict[int, dict]:
    out = {}
    for w in weeks:
        with urllib.request.urlopen(SCOREBOARD.format(season=season, week=w), timeout=20) as r:
            out[w] = json.load(r)
    return out


def row(p: dict, rw: int, owner: str | None, slot: str, mine: bool,
        rank: dict[str, int], sched: dict[str, dict[int, str]], weeks: list[int]) -> dict:
    """One table row off an enriched player dict (season lookup, injuries, exp, form applied)."""
    opps = [(w, sched.get(p["team"], {}).get(w)) for w in weeks]
    return {"name": p["name"], "pos": p["pos"], "team": p["team"], "owner": owner or "FA", "mine": mine,
            "slot": slot, "injury": p["injury"], "note": p.get("injury_note"), "ahead": p.get("ahead") or [],
            "exp": p.get("exp"), "wk": p.get("proj"), "ros": S.ros_proj(p, rw), "ros_sleeper": p.get("ros_sleeper"),
            "ros_cbs": p.get("ros_cbs"), "adj": S.ros_adj(p, rw), "form": p.get("form"),
            "opps": [(w, o, rank.get(o)) for w, o in opps if o]}


def ranked(rows: list[dict], pos: str, top: int, min_games: int, min_adj: float) -> list[dict]:
    """The position's rows with a window of at least `min_games` and an adjusted ROS worth reading,
    by pace over the model, best first."""
    keep = [r for r in rows if r["pos"] == pos and r["form"] and r["form"]["games"] >= min_games
            and r["adj"] >= min_adj and r["form"].get("pace_gap") is not None]
    return sorted(keep, key=lambda r: -r["form"]["pace_gap"])[:top]


def build(top: int = 14, min_games: int = 2, min_adj: float = 60.0) -> dict:
    lg = E.fresh_league()
    settings = E.load()
    week = lg.current_week
    rw = E.ros_week_for(lg, week)
    me = E.my_team_id(lg)
    src = E.weekly_sources(week, settings)
    seas = E._season_for(settings, None, rw)
    inj, w1 = E._injuries_for(None), E._week1_start(settings)
    home = E.home_by_team(lg, week)
    byes, weeks = E.bye_by_team(lg), E.form_weeks(week)
    snap_weeks = E._snaps_for(weeks, None)
    refs, exp = E.snap_refs(snap_weeks), E._exp_for(weeks, None)
    names = {t.team_id: t.team_name for t in lg.teams}

    boxes = {w: E._boxes_by_team(lg, w) for w in weeks}
    games: dict[int, list] = defaultdict(list)
    for w in weeks:
        for lineup in boxes[w].values():
            for bp in lineup:
                games[bp.playerId].extend(E.player_games(bp, [w], week))
    where = {bp.playerId: (tid, bp.slot_position) for tid, lineup in boxes[week].items() for bp in lineup}

    def enrich(players: list[dict], g: dict[int, list]) -> list[dict]:
        return E.apply_form(E.apply_exp(E.apply_home(E.apply_injuries(
            E.apply_season(E.merge_weekly(players, src), seas), inj, w1), home), exp), g, snap_weeks, rw, byes, refs)

    rostered = enrich([E.flatten_box_player(bp) for lineup in boxes[week].values() for bp in lineup], games)
    asks = [(pos, w) for pos in POSITIONS for w in weeks]
    with ThreadPoolExecutor(len(asks)) as pool:
        fetched = dict(zip(asks, pool.map(lambda a: lg.free_agents(week=a[1], size=POOL_SIZE, position=a[0]), asks)))
    frees: list[dict] = []
    for pos in POSITIONS:
        fg: dict[int, list] = defaultdict(list)
        for w in weeks:
            for obj in fetched[(pos, w)]:
                fg[obj.playerId].extend(E.player_games(obj, [w], week))
        frees += enrich([E.flatten_box_player(bp) for bp in fetched[(pos, week)]], fg)

    played = [w for w in weeks if w < week] + ([week] if rw > week else [])
    rank, sched = parse_scoreboard(fetch_scoreboards(int(settings.get("season") or SEASON), played))
    rows = []
    for p in rostered + frees:
        if p["pos"] not in POSITIONS:
            continue
        tid, slot = where.get(p["player_id"], (None, "FA"))
        rows.append(row(p, rw, names.get(tid) if tid else None, slot, tid == me, rank, sched, played))
    return {"week": week, "ros_week": rw, "top": top, "min_games": min_games,
            "positions": {pos: ranked(rows, pos, top, min_games, min_adj) for pos in POSITIONS}, "rows": rows}


def _f(x, n=1) -> str:
    return "-" if x is None else (f"{x:.{n}f}" if isinstance(x, (int, float)) else str(x))


def render(report: dict) -> str:
    lines = [f"ROS week {report['ros_week']} (scoring period {report['week']}); windows of {report['min_games']}+ games; "
             "pace = l3 x games left - model ROS; opp = his team's opponents, defensive rank 1 = stingiest"]
    for pos, rows in report["positions"].items():
        lines += ["", f"== {pos} ==",
                  f"{'player':22}{'tm':4}{'where':38}{'inj':5}{'exp':>4}{'g':>2}{'ppg':>6}{'beat':>6}{'pace':>6}"
                  f"{'ros':>6}{'adj':>6}{'snap':>5}{'opp':>5}{'tr':>5}{'tsn':>5}{'ypt':>5}  opponents"]
        for r in rows:
            fm = r["form"]
            where = ("ME " if r["mine"] else "") + (f"{r['owner'][:26]} {r['slot']}" if r["owner"] != "FA" else "FA")
            lines.append(f"{r['name'][:21]:22}{r['team']:4}{where[:37]:38}{r['injury'][:4]:5}{_f(r['exp'], 0):>4}"
                         f"{fm['games']:>2}{_f(fm['l3']):>6}{_f(fm['beat']):>6}{_f(fm['pace_gap'], 0):>6}{_f(r['ros'], 0):>6}"
                         f"{_f(r['adj'], 0):>6}{_f(fm['snap_pct'], 0):>5}{_f(fm['opp']):>5}{_f(fm['opp_trend']):>5}"
                         f"{_f(fm['tgt_snap'], 2):>5}{_f(fm['ypt']):>5}  " + " ".join(f"{o}#{k}" for _, o, k in r["opps"]))
            if r["ahead"]:
                lines.append(" " * 22 + "ahead: " + "; ".join(f"{a['name']} {a['status']} wk{a['return_week']}" for a in r["ahead"]))
            if r["note"]:
                lines.append(" " * 22 + "note: " + r["note"][:220])
    mine = sorted((r for r in report.get("rows", []) if r["mine"]), key=lambda r: (POSITIONS.index(r["pos"]), -r["adj"]))
    if mine:
        # the write-up names drops and judges the flex rule against these, so they are always printed
        lines += ["", "== my roster (usage-adjusted ROS, best first) ==",
                  f"{'player':22}{'pos':4}{'slot':10}{'inj':5}{'g':>2}{'ppg':>6}{'ros':>6}{'adj':>6}{'snap':>5}{'opp':>5}{'tr':>5}"]
        for r in mine:
            fm = r["form"] or {}
            lines.append(f"{r['name'][:21]:22}{r['pos']:4}{r['slot'][:9]:10}{r['injury'][:4]:5}{_f(fm.get('games'), 0):>2}"
                         f"{_f(fm.get('l3')):>6}{_f(r['ros'], 0):>6}{_f(r['adj'], 0):>6}{_f(fm.get('snap_pct'), 0):>5}"
                         f"{_f(fm.get('opp')):>5}{_f(fm.get('opp_trend')):>5}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--top", type=int, default=14)
    ap.add_argument("--min-games", type=int, default=2)
    ap.add_argument("--min-adj", type=float, default=60.0)
    ap.add_argument("--json", help="also write the full report (every row) here")
    a = ap.parse_args(argv)
    report = build(a.top, a.min_games, a.min_adj)
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(report, fh)
    print(render(report))


if __name__ == "__main__":
    main(sys.argv[1:])
