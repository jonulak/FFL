"""Projection sources beyond ESPN's box score and Sleeper (STRATEGY.md "Lineup" / "Waivers"): one
module per site, each a pure parse_* seam and a fetch through Cached. Every source is optional: a
failed or thin scrape serves the last good data for that key, else {} (the consensus runs on whoever
answered), and its health is kept in STATUS and the DB (`source_status`) for GET /sources/status and
the 24-hour notify detector. QB/RB/WR/TE only.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

import requests

from .. import db

log = logging.getLogger(__name__)
TTL_S = 1800
# fewer rows than this at a position is a broken scrape, not a projection (ffanalytics' FantasyPros
# scraper silently returned 10 a position in 2026)
MIN_ROWS = {"QB": 20, "RB": 20, "WR": 20, "TE": 10}
STATUS: dict[str, dict] = {}
_LOADED = False
_ALL: list["Cached"] = []


class Thin(Exception):
    pass


def check(data: dict[tuple[str, str], float], min_rows: dict[str, int] | None = None) -> dict[str, int]:
    """min_rows None means MIN_ROWS; a dict replaces it for this call. An empty result always fails."""
    if not data:
        raise Thin("empty scrape")
    minimum = MIN_ROWS if min_rows is None else min_rows
    counts: dict[str, int] = {}
    for _, pos in data:
        counts[pos] = counts.get(pos, 0) + 1
    thin = [p for p, m in minimum.items() if counts.get(p, 0) < m]
    if thin:
        raise Thin(f"thin scrape at {', '.join(thin)}: {counts}")
    return counts


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> None:
    """Once per process, before the first read-modify-write: fold the persisted record into STATUS so a
    restart does not overwrite other sources' history (or their failing_since)."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        saved = db.get_state("source_status", {}) or {}
        STATUS.update({k: v for k, v in saved.items() if k not in STATUS})
    except Exception as e:
        log.warning("source status not loaded: %s", e)


def _save() -> None:
    try:
        db.put_state("source_status", STATUS)
    except Exception as e:
        log.warning("source status not saved: %s", e)


def _ok(name: str, counts: dict[str, int]) -> None:
    _load()
    STATUS[name] = {"ok_at": _now(), "rows": counts, "error": None, "failing_since": None}
    _save()


def _failed(name: str, e: Exception) -> None:
    _load()
    prev = STATUS.get(name) or {}
    STATUS[name] = {"ok_at": prev.get("ok_at"), "rows": prev.get("rows", {}), "error": str(e),
                    "failing_since": prev.get("failing_since") or _now()}
    log.warning("source %s failed: %s", name, e)
    _save()


class Cached:
    """Per-key TTL cache keeping the last good value through an outage."""

    def __init__(self, name: str):
        self.name = name
        self._d: dict = {}
        _ALL.append(self)

    def get(self, key, fetch: Callable[[], dict], min_rows: dict[str, int] | None = None) -> dict:
        hit = self._d.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            data = fetch()
            counts = check(data, min_rows)
        except Exception as e:  # a parser surprise must fall back to last good too, not fail the page
            _failed(self.name, e)
            return hit[1] if hit else {}
        _ok(self.name, counts)
        self._d[key] = (time.time() + TTL_S, data)
        return data

    def expire(self) -> None:
        self._d = {k: (0.0, v) for k, (_, v) in self._d.items()}


def expire_all() -> None:
    """POST /refresh: refetch on next read, keep the last good data for an outage."""
    for c in _ALL:
        c.expire()


def reset() -> None:
    """Tests only: forget every cached value and the health record in memory."""
    for c in _ALL:
        c._d = {}
    global _LOADED
    STATUS.clear()
    _LOADED = False
