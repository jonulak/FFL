"""STRATEGY.md "Usage-adjusted ROS": K is a guess about noise, checked here rather than trusted.

The pool builder writes one snapshot per ROS week (`data/calibration/week-<w>.json`: what the blend
read for every player — the model, l3, the snap-weighted game count, the games left). This module
scores those snapshots against what the players then did, per K, on the per-game rate: the blend's
projected points per game against actual points per game since the snapshot. Run it once a few
weeks are in:

    uv run python -m ffl.calibrate            # every snapshot, K in 1..12 and the model alone
    uv run python -m ffl.calibrate 2 4 8      # the Ks to try

Everything but `main` is pure; `actuals_since` is the one ESPN call (player cards, in batches).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import season as S
from .config import DATA

CAL_DIR = DATA / "calibration"
DEFAULT_KS = [1, 2, 3, 4, 6, 8, 12, 1000]  # 1000 is the model alone, near enough
SIGMA_FILE = CAL_DIR / "sigma.json"


def load_sigma(path: Path = SIGMA_FILE) -> tuple[dict[str, float], str]:
    """The win meter's fitted spread per position over the fallback table, and which it is; a
    missing or unreadable file is the fallback, never an error (the meter still draws)."""
    try:
        fit = json.loads(path.read_text())
        return {**S.SIGMA_FALLBACK, **{k: float(v) for k, v in fit["sigma"].items()}}, "calibrated"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
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


def write_snapshot(path: Path, week: int, rows: list[dict]) -> bool:
    """The week's snapshot, written once: the first build of the ROS week is the one that stands
    (later builds of the same week have seen more of it). True when written."""
    path.mkdir(parents=True, exist_ok=True)
    out = path / f"week-{week}.json"
    if out.exists():
        return False
    out.write_text(json.dumps({"week": week, "k": S.K_PRIOR, "rows": rows}, indent=0))
    return True


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


def load_snapshots(path: Path = CAL_DIR) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(path.glob("week-*.json"))]


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


def main(argv: list[str]) -> None:
    from .espn_season import fresh_league
    ks = [float(a) for a in argv] or DEFAULT_KS
    snaps = load_snapshots()
    if not snaps:
        print(f"no snapshots in {CAL_DIR}: the pool builder writes one per ROS week")
        return
    lg = fresh_league()
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
    print(f"K in STRATEGY.md is {S.K_PRIOR}; change it there first, then season.K_PRIOR")


if __name__ == "__main__":
    main(sys.argv[1:])
