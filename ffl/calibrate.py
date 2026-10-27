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
from datetime import datetime

from . import db, notify
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
    from .consensus import combine, weights_for
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
                        p["proj"] = combine(vals, weights_for("week", p["pos"]))[0]
                rows += [{"pos": p["pos"], "pts": p["actual"], "proj": S.playable_proj(p)} for p in players]
                sides.append(players)
            if len(sides) == 2 and all(sides):
                games.append((sides[0], sides[1]))
    return rows, games


SHRINK_WEEKS = 4   # a fit from n weeks is (n * fitted + 4 * equal) / (n + 4)


def fit_weights(pairs: list[dict], shrink: int = SHRINK_WEEKS) -> dict[str, dict]:
    """Per position: each source's MAE and inverse-MSE weight against what happened, normalised and
    shrunk toward equal by the weeks behind it (the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`): weights are fitted, then
    approved by hand)."""
    by: dict[str, dict[str, list[float]]] = {}
    weeks: dict[str, set] = {}
    for r in pairs:
        by.setdefault(r["pos"], {}).setdefault(r["source"], []).append(r["points"] - r["actual"])
        weeks.setdefault(r["pos"], set()).add(r["week"])
    out = {}
    for pos, errs in by.items():
        mse = {s: max(sum(e * e for e in v) / len(v), 1e-6) for s, v in errs.items()}
        inv = {s: 1 / m for s, m in mse.items()}
        tot, eq, n = sum(inv.values()), 1 / len(inv), len(weeks[pos])
        out[pos] = {"weights": {s: round((n * inv[s] / tot + shrink * eq) / (n + shrink), 4) for s in inv},
                    "mae": {s: round(sum(abs(e) for e in v) / len(v), 2) for s, v in errs.items()},
                    "n": {s: len(v) for s, v in errs.items()}, "weeks": n}
    return out


def compare(pairs: list[dict], weights: dict[str, dict[str, float]]) -> dict[str, dict]:
    """The consensus' MAE per position with equal weights and with `weights`, over every (week, player)."""
    from .consensus import combine
    games: dict[tuple, dict] = {}
    for r in pairs:
        g = games.setdefault((r["week"], r["player_id"]), {"pos": r["pos"], "actual": r["actual"], "vals": {}})
        g["vals"][r["source"]] = r["points"]
    out: dict[str, dict] = {}
    for g in games.values():
        o = out.setdefault(g["pos"], {"equal": 0.0, "fitted": 0.0, "n": 0})
        o["equal"] += abs(combine(g["vals"])[0] - g["actual"])
        o["fitted"] += abs(combine(g["vals"], weights.get(g["pos"]))[0] - g["actual"])
        o["n"] += 1
    return {pos: {"equal": round(o["equal"] / o["n"], 2), "fitted": round(o["fitted"] / o["n"], 2), "n": o["n"]}
            for pos, o in out.items()}


def weekly_pairs(lg, rows: list[dict], *, ref) -> list[dict]:
    """projection_rows (kind week) joined to what each player scored that week, off the box scores."""
    from .espn_season import _boxes_by_team
    actual = {}
    for w in sorted({r["week"] for r in rows}):
        for lineup in _boxes_by_team(lg, w, ref=ref).values():
            for bp in lineup:
                if not getattr(bp, "on_bye_week", False):
                    actual[(w, bp.playerId)] = (bp.position, float(getattr(bp, "points", 0) or 0))
    return [{**r, "pos": actual[(r["week"], r["player_id"])][0], "actual": actual[(r["week"], r["player_id"])][1]}
            for r in rows if (r["week"], r["player_id"]) in actual]


def ros_pairs(lg, rows: list[dict], now: int, *, ref) -> list[dict]:
    """projection_rows (kind ros) as a per-game rate against his per-game points since that ROS week;
    only ROS weeks with at least one week played since."""
    from .espn_season import _boxes_by_team
    out = []
    for w in sorted({r["week"] for r in rows if r["week"] < now}):
        wr = [r for r in rows if r["week"] == w]
        pos = {bp.playerId: bp.position for lineup in _boxes_by_team(lg, w, ref=ref).values() for bp in lineup}
        got = actuals_since(lg, sorted({r["player_id"] for r in wr}), w, now - 1)
        for r in wr:
            pts, games = got.get(r["player_id"], (0.0, 0))
            if games and r["player_id"] in pos:
                out.append({**r, "pos": pos[r["player_id"]], "points": r["points"] / max(S.games_left(w, None), 1),
                            "actual": pts / games})
    return out


def _sources(argv: list[str]) -> None:
    if argv[:1] == ["--activate"] and len(argv) == 2:
        ok = db.activate_source_weights(argv[1])
        print(f"activated {argv[1]}" if ok else f"no fit {argv[1]}: see `ffl.calibrate sources`")
        return
    from . import leagues
    from .espn_season import fresh_league, ros_week_for
    wk, ros = [], []
    for ref in leagues.all_leagues():  # one fit over every league's recorded weeks
        lg = fresh_league(ref)
        done = ros_week_for(lg, lg.current_week)          # weeks before this one are fully played
        wk += weekly_pairs(lg, [r for r in db.projection_rows("week", ref=ref) if r["week"] < done], ref=ref)
        ros += ros_pairs(lg, db.projection_rows("ros", ref=ref), done, ref=ref)
    if not wk:
        print("no recorded weeks played yet: the notify tick records them (projection_rows)")
        return
    fit = {"week": fit_weights(wk), "ros": fit_weights(ros) if ros else {}}
    weights = {k: {pos: f["weights"] for pos, f in v.items()} for k, v in fit.items()}
    report = {"week": compare(wk, weights["week"]), "ros": compare(ros, weights["ros"]) if ros else {}}
    at = db.put_source_weights({**weights, "report": report, "fit": fit})
    for kind in ("week", "ros"):
        for pos, f in sorted(fit[kind].items()):
            r = report[kind][pos]
            maes = "  ".join(f"{s} {m}" for s, m in sorted(f["mae"].items(), key=lambda x: x[1]))
            print(f"{kind:>4} {pos:<3} {f['weeks']} wk  MAE {maes}  |  consensus equal {r['equal']} fitted {r['fitted']} (n {r['n']})")
    eq = sum(r["equal"] * r["n"] for r in report["week"].values())
    fi = sum(r["fitted"] * r["n"] for r in report["week"].values())
    print(f"fit {at} stored inactive. Activate: uv run python -m ffl.calibrate sources --activate {at}")
    if fi < eq:
        n = sum(r["n"] for r in report["week"].values())
        notify.app_notices([{"key": f"weights:{at}", "kind": "weights", "title": "Fitted source weights beat the plain average",
                         "body": f"Weekly MAE {fi / n:.2f} vs {eq / n:.2f} over {n}. Activate fit {at} to use them.",
                         "url": "/settings", "created_at": datetime.now().astimezone().isoformat(timespec='seconds')}])


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
    if argv[:1] == ["sources"]:
        _sources(argv[1:])
        return
    if argv[:1] == ["wednesday"]:   # the launchd job: the win meter's σ, then the source weights
        for name, fn in (("winprob", _winprob), ("sources", lambda: _sources([]))):
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — one failing must not skip the other; the log shows it
                print(f"{name} failed: {e}")
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
