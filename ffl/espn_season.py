"""ESPN + Sleeper I/O for the season, flattened to plain dicts for ffl.season.

Everything here touches the network except flatten_box_player / merge_weekly, which are the
pure seams the tests cover.
"""
from __future__ import annotations

import requests

from .adp import norm_name
from .config import SEASON
from .sleeper import TEAM_FIX, score

SLEEPER_WEEK = "https://api.sleeper.app/projections/nfl/{season}/{week}"
SLEEPER_POS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def flatten_box_player(bp, season_proj: float | None = None) -> dict:
    proj = float(getattr(bp, "projected_points", 0) or 0)
    return {"player_id": bp.playerId, "name": bp.name, "pos": bp.position, "team": bp.proTeam,
            "slot": bp.slot_position, "bye": bool(getattr(bp, "on_bye_week", False)),
            "injury": bp.injuryStatus or "ACTIVE", "proj_espn": proj, "proj_sleeper": None, "proj": proj,
            "spread": None, "espn_only": True, "actual": float(getattr(bp, "points", 0) or 0),
            "game_played": int(getattr(bp, "game_played", 0) or 0),
            "season_proj": float(season_proj if season_proj is not None else getattr(bp, "projected_total_points", 0) or 0),
            "season_proj_sleeper": None}


def fetch_sleeper_week(week: int, ks: dict) -> dict[tuple[str, str], float]:
    r = requests.get(SLEEPER_WEEK.format(season=SEASON, week=week),
                     params=[("season_type", "regular"), ("order_by", "pts_ppr")] + [("position[]", p) for p in SLEEPER_POS],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    out: dict[tuple[str, str], float] = {}
    for p in r.json():
        pl, st = p.get("player", {}), p.get("stats", {})
        pos = pl.get("position")
        if pos not in SLEEPER_POS or not st:
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
        key = (p["team"], "D/ST") if p["pos"] == "D/ST" else (norm_name(p["name"]), p["pos"])
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
