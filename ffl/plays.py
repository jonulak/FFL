"""The play feed: one event per points change of a starter in my matchup, from the live feed.

An event is derived, not received: the feed pushes a stats patch for a player, `live` rescores
him, and the difference is the event. The play text is the current last play of that player's
game at that moment — usually the play that moved him, occasionally the next one, because the
stats and the play text travel in different frames. Events are appended to a JSONL file per
week under data/live/ and reloaded at boot, so a deploy mid-slate does not empty the feed.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from .config import DATA

PLAYS_DIR = DATA / "live"


def roster_index(week: dict) -> dict[int, dict]:
    """player_id -> {name, pos, team, side, team_id} for every *starter* in a week payload."""
    out: dict[int, dict] = {}
    for side in ("me", "opp"):
        s = week.get(side)
        if not s:
            continue
        for p in s.get("lineup", []):
            out[p["player_id"]] = {"name": p["name"], "pos": p["pos"], "team": p["team"],
                                   "side": side, "team_id": s["team_id"]}
    return out


def _game_of(doc: dict, player_id: int) -> dict | None:
    gid = (doc.get("statistics", {}).get(str(player_id)) or {}).get("gameId")
    return next((e for e in doc.get("events", []) if e.get("id") == gid), None) if gid else None


def events_from(before: dict[int, float], after: dict[int, float], doc: dict, roster: dict[int, dict],
                at: datetime, week: int) -> list[dict]:
    """The play-feed rows for one rescoring: every rostered starter whose points moved."""
    rows = []
    for pid, who in roster.items():
        new, old = after.get(pid), before.get(pid)
        if new is None or new == (old if old is not None else 0.0):
            continue
        game = _game_of(doc, pid) or {}
        play = game.get("lastPlay") or {}
        rows.append({"at": at.isoformat(timespec="seconds"), "week": week, "player_id": pid, **who,
                     "delta": round(new - (old or 0.0), 2), "total": round(new, 2),
                     "text": (play.get("text") or "").strip() or None, "play_id": play.get("playId"),
                     "quarter": game.get("period"), "clock": game.get("clock"), "game_id": game.get("id")})
    return rows


class Store:
    """The week's events, newest last in memory, appended to `path` as they happen."""

    def __init__(self, path: Path):
        self.path = path
        self.rows: list[dict] = []
        self._lock = threading.Lock()
        if path.exists():
            with path.open() as f:
                self.rows = [json.loads(line) for line in f if line.strip()]

    def add(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._lock:
            self.rows.extend(rows)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")

    def latest(self, n: int | None = None) -> list[dict]:
        with self._lock:
            rows = self.rows[::-1]
        return rows[:n] if n else rows


def week_path(week: int) -> Path:
    return PLAYS_DIR / f"plays-week-{week}.jsonl"
