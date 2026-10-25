"""the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`), props trial (weeks 4-6): betting player props as a third weekly source.

Recorded, not used: the Roster call stays on ESPN + Sleeper until `score` says the three-source mean
beat it by the bar fixed before the trial (lower pooled MAE and better in two of the three weeks).

A pull snapshots every upcoming game's props off The Odds API (free tier, `ODDS_API_KEY` in .env)
and, at the same moment, ESPN's and Sleeper's projections for every rostered QB/RB/WR/TE:

    uv run python -m ffl.props pull thu     # launchd: Thursday before the night game
    uv run python -m ffl.props pull sun     # launchd: Sunday 11:30 ET
    uv run python -m ffl.props score        # after the trial: MAE by week, the verdict, my lineup flips

A player's props number is Sleeper's stat line with each stat a market prices replaced by the
market's consensus (yardage and receptions at the median line; TDs through a Poisson mean off the
devigged price), rescored to the league. Each game is judged off the last snapshot before its
kickoff, so a Thursday game is read from Thursday's lines. Snapshots are the DB's `props_snapshots`, and the raw
responses are kept in `props_raw` so a different conversion can be scored later without spending credits.

Everything but `pull`, `score_main` and the two fetches is pure.
"""
from __future__ import annotations

import math
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

import requests

from . import db
from . import season as S
from .adp import norm_name
from .sleeper import score as score_stats

TRIAL_WEEKS = (4, 5, 6)
ODDS = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
# market -> the Sleeper stat it replaces ("any_td" is split into rush/rec by props_stats)
MARKETS = {"player_pass_yds": "pass_yd", "player_pass_tds": "pass_td", "player_rush_yds": "rush_yd",
           "player_reception_yds": "rec_yd", "player_receptions": "rec", "player_anytime_td": "any_td"}
LINE_MARKETS = {"pass_yd", "rush_yd", "rec_yd", "rec"}  # read at the median line
SKILL = {"QB", "RB", "WR", "TE"}


# ---------- prices -> expected stats (pure) ----------

def devig(over: float, under: float) -> float:
    """P(over) from two decimal prices, the overround split proportionally."""
    a, b = 1 / over, 1 / under
    return a / (a + b)


def poisson_mean_over(line: float, p_over: float) -> float:
    """The Poisson mean whose P(X > line) is p_over (bisection; the tail rises with the mean)."""
    k = math.floor(line)

    def tail(lam: float) -> float:
        return 1 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))

    lo, hi = 0.0, 20.0
    for _ in range(80):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if tail(mid) < p_over else (lo, mid)
    return (lo + hi) / 2


def consensus(event: dict) -> dict[str, dict[str, float]]:
    """One event's props across books -> {normalized name: {stat: expected value}}.

    Lines: the median of the books' lines. Pass TDs: the median of each book's Poisson mean off its
    devigged over. Anytime TD: devigged when a book prices No; most price only Yes, and then the
    longest Yes price (the least vig) is the one read. λ = -ln(1 - p).
    """
    lines: dict[tuple[str, str], list[float]] = {}
    for book in event.get("bookmakers") or []:
        for m in book.get("markets") or []:
            stat = MARKETS.get(m.get("key"))
            if stat is None:
                continue
            by_who: dict[str, dict[str, dict]] = {}
            for o in m.get("outcomes") or []:
                by_who.setdefault(o.get("description") or "", {})[o.get("name")] = o
            for who, sides in by_who.items():
                key = (norm_name(who), stat)
                if stat in LINE_MARKETS and "Over" in sides and sides["Over"].get("point") is not None:
                    lines.setdefault(key, []).append(float(sides["Over"]["point"]))
                elif stat == "pass_td" and "Over" in sides and "Under" in sides:
                    p = devig(sides["Over"]["price"], sides["Under"]["price"])
                    lines.setdefault(key, []).append(poisson_mean_over(float(sides["Over"]["point"]), p))
                elif stat == "any_td" and "Yes" in sides:
                    p = (devig(sides["Yes"]["price"], sides["No"]["price"]) if "No" in sides
                         else 1 / sides["Yes"]["price"])
                    lines.setdefault(key, []).append(-math.log(1 - min(p, 0.99)))
    out: dict[str, dict[str, float]] = {}
    for (who, stat), vals in lines.items():
        out.setdefault(who, {})[stat] = min(vals) if stat == "any_td" else statistics.median(vals)
    return out


def props_stats(sleeper: dict, market: dict, pos: str) -> dict:
    """Sleeper's stat line with each stat the market prices replaced. A QB's anytime TD is a rushing
    TD; anyone else's is split by Sleeper's rush/rec shares (receiving when Sleeper has neither,
    rushing for an RB)."""
    out = dict(sleeper)
    for stat, v in market.items():
        if stat != "any_td":
            out[stat] = v
    if "any_td" in market:
        lam = market["any_td"]
        if pos == "QB":
            out["rush_td"] = lam
        else:
            ru, re_ = float(sleeper.get("rush_td") or 0), float(sleeper.get("rec_td") or 0)
            if ru + re_ > 0:
                out["rush_td"], out["rec_td"] = lam * ru / (ru + re_), lam * re_ / (ru + re_)
            else:
                out["rush_td" if pos == "RB" else "rec_td"] = lam
    return out


def sleeper_stat_lines(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Sleeper's raw weekly rows -> {(normalized name, pos): stat line}, skill players with a game."""
    out = {}
    for r in rows:
        pl, st = r.get("player") or {}, r.get("stats") or {}
        if pl.get("position") in SKILL and st.get("gp"):
            out[(norm_name(f"{pl.get('first_name', '')} {pl.get('last_name', '')}"), pl["position"])] = st
    return out


def baseline(players: list[dict], sleeper: dict[tuple[str, str], float]) -> list[dict]:
    """The trial's bar is fixed against ESPN + Sleeper (the league's strategy, "Props trial" (`uv run python -m ffl.strategy show`)): their mean as
    `proj`, each kept beside it, whatever the live board now averages."""
    from .espn_season import source_key
    out = []
    for p in players:
        sl = sleeper.get(source_key(p))
        espn = p["proj_espn"] if p["proj_espn"] > 0 else None
        have = [x for x in (espn, sl) if x is not None]
        out.append({**p, "proj_sleeper": sl, "proj": round(sum(have) / len(have), 1) if have else p["proj_espn"]})
    return out


def snapshot_rows(players: list[dict], stats: dict, market: dict, ks: dict) -> list[dict]:
    """Rostered skill players (merged ESPN + Sleeper rows) with the props number beside the mean.
    `proj3` is the mean of whichever of ESPN (>0), Sleeper and props he has; no line: `proj`."""
    rows = []
    for p in players:
        if p["pos"] not in SKILL:
            continue
        key = norm_name(p["name"])
        mk = market.get(key) or {}
        pp = round(score_stats(props_stats(stats.get((key, p["pos"]), {}), mk, p["pos"]), ks[p["pos"]]), 1) if mk and p["pos"] in ks else None
        srcs = [v for v in (p["proj_espn"] if p["proj_espn"] > 0 else None, p.get("proj_sleeper"), pp) if v is not None]
        proj3 = round(sum(srcs) / len(srcs), 1) if pp is not None else p["proj"]
        rows.append({k: p.get(k) for k in ("player_id", "name", "pos", "team", "fantasy_team", "slot", "bye",
                                           "injury", "kickoff", "proj_espn", "proj_sleeper", "proj")}
                    | {"proj_props": pp, "proj3": proj3, "markets": sorted(mk)})
    return rows


# ---------- scoring the trial (pure) ----------

def _when(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def week_end(now: datetime) -> datetime:
    """The coming Tuesday 12:00 UTC: after Monday night's game, before next week's Thursday one."""
    days = (1 - now.weekday()) % 7 or 7
    return (now + timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0)


def pick_rows(snaps: list[dict]) -> list[dict]:
    """One row per player for a week: from the last snapshot taken before his kickoff."""
    best: dict[int, tuple[datetime, dict]] = {}
    for snap in snaps:
        taken = _when(snap["taken_at"])
        for r in snap["rows"]:
            if not r.get("kickoff") or taken >= _when(r["kickoff"]):
                continue
            hit = best.get(r["player_id"])
            if hit is None or taken > hit[0]:
                best[r["player_id"]] = (taken, r)
    return [r for _, r in best.values()]


def score_week(rows: list[dict], actual: dict[int, tuple[float, int]]) -> dict:
    """MAE of the two-source and three-source means over players who had a props number and played.
    Everyone else has proj3 == proj, the same miss either way."""
    pairs = [(r, actual[r["player_id"]][0]) for r in rows
             if r.get("proj_props") is not None and actual.get(r["player_id"], (0, 0))[1] > 0]
    n = len(pairs)
    mae = lambda k: round(sum(abs(r[k] - a) for r, a in pairs) / n, 2) if n else None
    return {"players": n, "mae2": mae("proj"), "mae3": mae("proj3")}


def verdict(weeks: list[dict]) -> dict:
    """The bar fixed before the trial: lower pooled MAE and better in two of the three weeks.
    `passes` is None until all three weeks are scored."""
    scored = [w for w in weeks if w["players"]]
    n = sum(w["players"] for w in scored)
    pool = lambda k: round(sum(w[k] * w["players"] for w in scored) / n, 2) if n else None
    wins = sum(w["mae3"] < w["mae2"] for w in scored)
    out = {"mae2": pool("mae2"), "mae3": pool("mae3"), "weeks_better": wins, "weeks": len(scored)}
    out["passes"] = None if len(scored) < len(TRIAL_WEEKS) else bool(out["mae3"] < out["mae2"] and wins >= 2)
    return out


def lineup_flips(rows: list[dict], actual: dict[int, tuple[float, int]], settings: dict) -> dict:
    """My skill starters by `proj` against by `proj3`: who props would have swapped, and the points
    that swap scored (positive: props would have helped)."""
    starters = lambda k: {e["player_id"] for e in S.optimal_lineup([r | {"proj": r[k]} for r in rows], settings)
                          if e["player_id"] is not None}
    two, three = starters("proj"), starters("proj3")
    pts = lambda ids: sum(actual.get(i, (0.0, 0))[0] for i in ids)
    return {"in": sorted(three - two), "out": sorted(two - three), "gain": round(pts(three - two) - pts(two - three), 1)}


# ---------- I/O ----------

def _get(url: str, **params) -> tuple[object, dict]:
    r = requests.get(url, params={"apiKey": os.environ["ODDS_API_KEY"], **params}, timeout=30)
    r.raise_for_status()
    return r.json(), r.headers


def fetch_events() -> list[dict]:
    """Upcoming NFL events (free: the events endpoint costs no credits)."""
    return _get(f"{ODDS}/events")[0]


def fetch_event_props(event_id: str) -> tuple[dict, int | None]:
    """One event's props, and the credits left after it (cost: one per market returned)."""
    data, h = _get(f"{ODDS}/events/{event_id}/odds", regions="us", markets=",".join(MARKETS), oddsFormat="decimal")
    left = h.get("x-requests-remaining")
    return data, int(float(left)) if left is not None else None


def pull(kind: str, force: bool = False) -> None:
    from .espn_season import _boxes_by_team, fetch_sleeper_rows, flatten_box_player, fresh_league, parse_sleeper_rows
    from .settings import load, rescoring
    if not os.environ.get("ODDS_API_KEY"):
        print("ODDS_API_KEY is not set in .env: nothing pulled")
        return
    lg = fresh_league()
    week = lg.current_week
    if week not in TRIAL_WEEKS:
        print(f"week {week} is outside the props trial {TRIAL_WEEKS}: nothing pulled")
        return
    if db.props_snapshot_exists(week, kind) and not force:
        print(f"week {week} {kind} is stored (--force to spend the credits again)")
        return
    now = datetime.now(timezone.utc)
    end = week_end(now)
    events = [e for e in fetch_events() if now < _when(e["commence_time"].replace("Z", "+00:00")) < end]
    market: dict[str, dict[str, float]] = {}
    left = None
    for e in events:
        if left is not None and left < len(MARKETS):
            print(f"stopping: {left} credits left, an event can cost {len(MARKETS)}")
            break
        data, left = fetch_event_props(e["id"])
        db.put_props_raw(week, kind, e["id"], data)
        for who, stats in consensus(data).items():
            market.setdefault(who, {}).update(stats)
    settings = load()
    ks = rescoring(settings)
    sl_rows = fetch_sleeper_rows(week)
    players = []
    for tid, lineup in _boxes_by_team(lg, week).items():
        players += [flatten_box_player(bp) | {"fantasy_team": tid} for bp in lineup]
    players = baseline(players, parse_sleeper_rows(sl_rows, ks))
    rows = snapshot_rows(players, sleeper_stat_lines(sl_rows), market, ks)
    db.put_props_snapshot(week, kind, {"week": week, "kind": kind, "taken_at": now.isoformat(),
                                       "events": len(events), "credits_left": left, "rows": rows})
    lined = sum(r["proj_props"] is not None for r in rows)
    print(f"week {week} {kind}: {len(events)} games, {lined}/{len(rows)} rostered players with a line, "
          f"{left} credits left (stored)")


def score_main() -> None:
    from .calibrate import actuals_since
    from .espn_season import fresh_league, my_team_id
    from .settings import load
    by_week: dict[int, list[dict]] = {}
    for snap in db.props_snapshot_docs():
        by_week.setdefault(snap["week"], []).append(snap)
    if not by_week:
        print("no props snapshots stored")
        return
    lg = fresh_league()
    me, settings = my_team_id(lg), load()
    weeks = []
    for w, snaps in sorted(by_week.items()):
        if w >= lg.current_week:
            print(f"week {w}: not over yet")
            continue
        rows = pick_rows(snaps)
        actual = actuals_since(lg, [r["player_id"] for r in rows], w, w)
        s = score_week(rows, actual)
        weeks.append(s)
        f = lineup_flips([r for r in rows if r["fantasy_team"] == me], actual, settings)
        names = {r["player_id"]: r["name"] for r in rows}
        swap = "no change"
        if f["in"]:
            swap = ", ".join([f"{names[i]} in" for i in f["in"]] + [f"{names[i]} out" for i in f["out"]])
            swap += f" ({f['gain']:+} pts)"
        print(f"week {w}: MAE {s['mae2']} (ESPN+Sleeper) vs {s['mae3']} (+props) over {s['players']} players;"
              f" my lineup: {swap}")
    v = verdict(weeks)
    state = {None: "not yet (three weeks are the bar)", True: "PASSES", False: "fails"}[v["passes"]]
    print(f"pooled: {v['mae2']} vs {v['mae3']}, props better in {v['weeks_better']} of {v['weeks']} weeks -> {state}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["pull"] and len(args) >= 2:
        pull(args[1], force="--force" in args)
    elif args[:1] == ["score"]:
        score_main()
    else:
        print(__doc__)
