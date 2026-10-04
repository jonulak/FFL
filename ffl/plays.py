"""The play feed: one event per points change of any league starter, from the live feed.

An event is derived, not received: the feed pushes a stats patch for a player, `live` rescores
him, and the difference is the event. The play text comes from `PlayCache`: the stats and the
play text travel in different frames — the play first, the stats one to three frames later,
by which time the game's `lastPlay` is often already the *next* play (a field goal was once
captioned with the timeout after it). Every play the feed shows (`lastPlay`, the current
drive's `plays`, the game's `scoringPlays`) names its players by id, so the cache remembers
them per game and an event takes the newest remembered play naming the player; `lastPlay` is
only the fallback (D/ST rows, stat corrections). Events are appended to a JSONL file per week
under data/live/ and reloaded at boot, so a deploy mid-slate does not empty the feed.

Every matchup's starters are followed, so every board has a feed. A row carries the fantasy
team it scored for (`team_id`), not a side: a team plays one game a week, so a board takes the
rows of its two teams (`Store.latest(teams=)`) and `for_board` says which side each is on.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from .config import DATA

PLAYS_DIR = DATA / "live"


def roster_index(league: dict) -> dict[int, dict]:
    """player_id -> {name, pos, team, team_id} for every starter in every game of a
    `espn_season.league_starters` payload."""
    out: dict[int, dict] = {}
    for game in league.get("games", []):
        for s in game:
            for p in s.get("lineup", []):
                out[p["player_id"]] = {"name": p["name"], "pos": p["pos"], "team": p["team"], "team_id": s["team_id"]}
    return out


def for_board(rows: list[dict], left: int) -> list[dict]:
    """The rows with `side` set for one board: "me" for the left team (`left`), else "opp".
    Rows written before the feed went league-wide carry a side already; the board's wins."""
    return [{**r, "side": "me" if r.get("team_id") == left else "opp"} for r in rows]


def _game_of(doc: dict, player_id: int) -> dict | None:
    gid = (doc.get("statistics", {}).get(str(player_id)) or {}).get("gameId")
    return next((e for e in doc.get("events", []) if e.get("id") == gid), None) if gid else None


def _clock(play: dict, game: dict) -> str | None:
    """A play's clock is seconds remaining (560.0); the game's is already 'm:ss'."""
    secs = play.get("clock")
    if isinstance(secs, (int, float)):
        return f"{int(secs) // 60}:{int(secs) % 60:02d}"
    return game.get("clock")


class PlayCache:
    """The last `keep` plays of each game, by play id, gathered from every document seen.

    A drive's `plays` list is replaced when the drive ends, so the play that ended it (a field
    goal, a turnover) is gone from the doc before its stats arrive; the cache keeps it.
    """

    def __init__(self, keep: int = 80):
        self.keep = keep
        self.plays: dict[str, dict[str, dict]] = {}  # game id -> play id -> play

    def remember(self, doc: dict) -> None:
        for game in doc.get("events", []):
            seen = [game.get("lastPlay") or {}]
            seen += (game.get("drive") or {}).get("plays") or []
            seen += game.get("scoringPlays") or []
            known = self.plays.setdefault(game.get("id"), {})
            for play in seen:
                if play.get("playId"):
                    known[str(play["playId"])] = play
            if len(known) > self.keep:
                for pid in sorted(known, key=int)[:-self.keep]:
                    del known[pid]

    def play_for(self, game_id: str, player_id: int) -> dict | None:
        """The newest remembered play of the game that names the player."""
        known = self.plays.get(game_id) or {}
        for pid in sorted(known, key=int, reverse=True):
            if any(str(p.get("playerId")) == str(player_id) for p in known[pid].get("players") or []):
                return known[pid]
        return None


def events_from(before: dict[int, float], after: dict[int, float], doc: dict, roster: dict[int, dict],
                at: datetime, week: int, cache: PlayCache | None = None) -> list[dict]:
    """The play-feed rows for one rescoring: every rostered starter whose points moved.

    `match` says where the caption came from: "player" (a remembered play naming him) or
    "last" (the game's current last play, which may already be the next one)."""
    rows = []
    for pid, who in roster.items():
        new, old = after.get(pid), before.get(pid)
        if new is None or new == (old if old is not None else 0.0):
            continue
        game = _game_of(doc, pid) or {}
        play = cache.play_for(game.get("id"), pid) if cache is not None else None
        match = "player" if play else "last"
        play = play or game.get("lastPlay") or {}
        rows.append({"at": at.isoformat(timespec="seconds"), "week": week, "player_id": pid, **who,
                     "delta": round(new - (old or 0.0), 2), "total": round(new, 2),
                     "text": (play.get("text") or "").strip() or None, "play_id": play.get("playId"),
                     "quarter": play.get("period") or game.get("period"), "clock": _clock(play, game),
                     "game_id": game.get("id"), "match": match})
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

    def latest(self, n: int | None = None, teams: set[int] | None = None) -> list[dict]:
        """Newest first; with `teams`, only the rows scored for those fantasy teams (one board)."""
        with self._lock:
            rows = self.rows[::-1]
        if teams is not None:
            rows = [r for r in rows if r.get("team_id") in teams]
        return rows[:n] if n else rows


def week_path(week: int) -> Path:
    return PLAYS_DIR / f"plays-week-{week}.jsonl"
