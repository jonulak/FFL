"""the league's strategy, "Usage-adjusted ROS" (`uv run python -m ffl.strategy show`): K is a guess about noise, checked here rather than trusted.

The pool builder writes one snapshot per ROS week (the DB's calibration tables: what the blend
read for every player — the model, l3, the snap-weighted game count, the games left). This module
scores those snapshots against what the players then did, per K, on the per-game rate: the blend's
projected points per game against actual points per game since the snapshot. Run it once a few
weeks are in:

    uv run python -m ffl.calibrate            # every snapshot, K in 1..12 and the model alone
    uv run python -m ffl.calibrate 2 4 8      # the Ks to try
    uv run python -m ffl.calibrate winprob    # the win meter's σ per position, and its Brier

The win meter (season.win_prob) is checked the same way: `winprob` fits the spread of points
around the projection per position from every league starter-week so far into
the DB (`sigma_fits`), and scores the pregame odds of the games already played.

Everything but `main` is pure; `actuals_since` and `starter_weeks` are the ESPN calls.
"""
from __future__ import annotations

import statistics
import sys

from . import db
from . import season as S

DEFAULT_KS = [1, 2, 3, 4, 6, 8, 12, 1000]  # 1000 is the model alone, near enough
MIN_ROWS = 10  # starter-weeks a position needs before its fitted σ replaces the fallback


def load_sigma() -> tuple[dict[str, float], str]:
    """The win meter's fitted spread per position over the fallback table, and which it is; no fit
    yet, a DB error or a malformed stored fit is the fallback, never an error (the meter still draws)."""
    try:
        fit = db.latest_sigma()
        if not fit:
            return dict(S.SIGMA_FALLBACK), "fallback"
        return {**S.SIGMA_FALLBACK, **{k: float(v) for k, v in fit["sigma"].items()}}, "calibrated"
    except (db.DBError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"sigma unreadable, using the fallback: {e}")
        return dict(S.SIGMA_FALLBACK), "fallback"


def snapshot_rows(positions: dict[str, list[dict]], week: int) -> list[dict]:
    """What the blend read, per player, off a built pool (season.waiver_deltas rows by position)."""
    rows = []
    for pos_rows in positions.values():
        for r in pos_rows:
            f = r.get("form") or {}
            rows.append({"player_id": r["player_id"], "name": r["name"], "pos": r["pos"],
                         "model": r["ros_proj"], "l3": f.get("l3") if f.get("weight") else None,
                         "weight": float(f.get("weight") or 0.0),
                         "games_left": int(f.get("games_left") or S.games_left(week, None))})
    return rows


def write_snapshot(week: int, rows: list[dict], *, ref) -> bool:
    """The week's snapshot, written once: the first build of the ROS week is the one that stands
    (later builds of the same week have seen more of it). True when written. `ref` is the league."""
    return db.put_snapshot(week, S.K_PRIOR, rows, ref=ref)


def blend(model: float, l3: float | None, weight: float, games_left: int, k: float) -> float:
    """season.ros_adj's arithmetic over the snapshot's numbers, for any K."""
    if not weight or l3 is None or games_left <= 0:
        return float(model)
    return round(games_left * (weight * l3 + k * model / games_left) / (weight + k), 1)


def score(snapshots: list[dict], actual: dict[int, tuple[float, int]], ks: list[float]) -> list[dict]:
    """Mean absolute error of the blend's points per game against actual points per game since
    the snapshot, per K, over every (snapshot, player) with a window and at least one game since.
    `actual` is player id -> (points since the snapshot week, games played since)."""
    out = []
    for k in ks:
        errs = []
        for snap in snapshots:
            for r in snap["rows"]:
                pts, games = actual.get(r["player_id"], (0.0, 0))
                if not r.get("weight") or r.get("l3") is None or games <= 0 or r["games_left"] <= 0:
                    continue
                pred_pg = blend(r["model"], r["l3"], r["weight"], r["games_left"], k) / r["games_left"]
                errs.append(abs(pred_pg - pts / games))
        out.append({"k": k, "mae": round(sum(errs) / len(errs), 2) if errs else None, "players": len(errs)})
    return out


def load_snapshots(*, ref) -> list[dict]:
    return db.snapshots(ref=ref)


def actuals_since(lg, ids: list[int], from_week: int, to_week: int) -> dict[int, tuple[float, int]]:
    """Points and games played from `from_week` through `to_week`, off ESPN's player cards in
    batches. A game is a week he was active in (espn_season.game_from_stats), so a bye or a week
    out does not count as a zero."""
    from .espn_season import player_games
    out: dict[int, tuple[float, int]] = {}
    weeks = list(range(from_week, to_week + 1))
    for i in range(0, len(ids), 50):
        got = lg.player_info(playerId=ids[i:i + 50]) or []
        for pl in (got if isinstance(got, list) else [got]):
            games = [g for g in player_games(pl, weeks, to_week + 1) if g["active"] and g["final"]]
            out[pl.playerId] = (round(sum(g["pts"] for g in games), 1), len(games))
    return out


def fit_sigma(rows: list[dict]) -> dict:
    """The spread of points around the projection by position, over starter-weeks that had one;
    a position with fewer than MIN_ROWS keeps the fallback σ."""
    misses: dict[str, list[float]] = {}
    for r in rows:
        if r["proj"] > 0:
            misses.setdefault(r["pos"], []).append(r["pts"] - r["proj"])
    keep = {pos: d for pos, d in misses.items() if len(d) >= MIN_ROWS}
    return {"sigma": {pos: round(statistics.stdev(d), 2) for pos, d in keep.items()},
            "n": {pos: len(d) for pos, d in keep.items()}}


def brier(games: list[tuple[float, float]]) -> float | None:
    """Mean squared miss of (probability, outcome) pairs; 0.25 is a coin flip."""
    return round(sum((p - o) ** 2 for p, o in games) / len(games), 4) if games else None


def write_sigma(fit: dict, weeks: list[int]) -> None:
    db.put_sigma({**fit, "weeks": weeks})


def starter_weeks(lg, settings: dict, weeks: list[int], *, ref) -> tuple[list[dict], list[tuple[list[dict], list[dict]]]]:
    """Every league starter of the given weeks as {pos, pts, proj} (proj the consensus the tick
    recorded, else ESPN + Sleeper; zero when he could not play), and each game's two starting lineups."""
    from .consensus import combine
    from .espn_season import _sleeper_for, box_scores, flatten_box_player, merge_weekly
    stored: dict[tuple[int, int], dict[str, float]] = {}
    for r in db.projection_rows("week", ref=ref):
        stored.setdefault((r["week"], r["player_id"]), {})[r["source"]] = r["points"]
    rows, games = [], []
    for w in weeks:
        sl = _sleeper_for(w, settings)
        for m in box_scores(lg, w, ref=ref):
            sides = []
            for lineup in (m.away_lineup, m.home_lineup):
                starters = [bp for bp in lineup or [] if getattr(bp, "slot_position", "BE") not in S.BENCH_SLOTS]
                players = merge_weekly([flatten_box_player(bp) for bp in starters], {"sleeper": sl})
                for p in players:   # what the meter read that week, when the tick recorded it
                    if vals := stored.get((w, p["player_id"])):
                        p["proj"] = combine(vals)[0]
                rows += [{"pos": p["pos"], "pts": p["actual"], "proj": S.playable_proj(p)} for p in players]
                sides.append(players)
            if len(sides) == 2 and all(sides):
                games.append((sides[0], sides[1]))
    return rows, games


def _winprob() -> None:
    """One σ fit over every league's starters (each scored on its own table), and its Brier."""
    from . import leagues
    from .espn_season import fresh_league
    from .settings import load
    rows, games, weeks = [], [], set()
    for ref in leagues.all_leagues():
        lg = fresh_league(ref)
        w = list(range(1, lg.current_week))
        r, g = starter_weeks(lg, load(ref), w, ref=ref)
        rows, games, weeks = rows + r, games + g, weeks | set(w)
    weeks = sorted(weeks)
    fit = fit_sigma(rows)
    write_sigma(fit, weeks)
    sigma, _ = load_sigma()
    print(f"weeks {weeks[0]}..{weeks[-1]}:" if weeks else "no weeks played yet")
    for pos in sorted(fit["sigma"]):
        print(f"  {pos:<4} σ {fit['sigma'][pos]:>5}  over {fit['n'][pos]} starter-weeks")

    def pregame(side: list[dict]) -> list[dict]:  # every starter's whole game still ahead
        return [{**p, "actual": 0.0, "game_played": 0} for p in side]

    def result(a: list[dict], b: list[dict]) -> float:
        d = round(sum(p["actual"] for p in a) - sum(p["actual"] for p in b), 2)
        return 1.0 if d > 0 else 0.0 if d < 0 else 0.5

    scored = [(S.win_prob(pregame(a), pregame(b), sigma), result(a, b)) for a, b in games]
    print(f"Brier {brier(scored)} over {len(scored)} games (coin flip 0.25; in-sample, so optimistic)")
    print("wrote the fit to the DB (sigma_fits)")


def main(argv: list[str]) -> None:
    if argv[:1] == ["wednesday"]:   # the weekly job: the win meter's σ
        _winprob()
        return
    if argv[:1] == ["winprob"]:
        _winprob()
        return
    from . import leagues
    from .espn_season import fresh_league
    ref = leagues.acting()  # the K check is one league's snapshots: FFL_USER's (FFL_LEAGUE, else their default)
    ks = [float(a) for a in argv] or DEFAULT_KS
    snaps = load_snapshots(ref=ref)
    if not snaps:
        print("no calibration snapshots yet: the pool builder writes one per ROS week")
        return
    lg = fresh_league(ref)
    now = lg.current_week
    per_snap: list[list[dict]] = []
    for snap in snaps:
        # since the snapshot's week, up to the last week with games in the book
        ids = [r["player_id"] for r in snap["rows"]]
        rows = score([snap], actuals_since(lg, ids, snap["week"], now), ks)
        per_snap.append(rows)
        print(f"week {snap['week']} snapshot ({len(ids)} players), scored through week {now}:")
        for r in rows:
            print(f"  K={r['k']:>6g}  MAE {r['mae'] if r['mae'] is not None else '—':>5}  pts/game over {r['players']} players")
    if len(snaps) > 1:
        print("all snapshots (players-weighted):")
        for i, k in enumerate(ks):
            n = sum(rows[i]["players"] for rows in per_snap)
            mae = round(sum(rows[i]["mae"] * rows[i]["players"] for rows in per_snap if rows[i]["mae"] is not None) / n, 2) if n else None
            print(f"  K={k:>6g}  MAE {mae if mae is not None else '—':>5}  over {n}")
    print(f"K in the league's strategy is {S.K_PRIOR}; change it in a new strategy version (a migration) first, then season.K_PRIOR")


if __name__ == "__main__":
    main(sys.argv[1:])
