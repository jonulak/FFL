"""Projection sources beyond ESPN's box score and Sleeper (the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`) / "Waivers"): one
module per site, each a pure parse_* seam and a fetch through Cached. Every source is optional: a
failed or thin scrape serves the last good data for that key, else {} (the consensus runs on whoever
answered), and its health is kept in STATUS and the DB (`source_status`) for GET /sources/status and
the 24-hour notify detector. QB/RB/WR/TE only.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from typing import Callable

import requests

from .. import db
from ..flight import Flight

log = logging.getLogger(__name__)
TTL_S = 1800
RETRY_S = 300   # after a failure: serve last good this long before trying the site again
# fewer rows than this at a position is a broken scrape, not a projection (ffanalytics' FantasyPros
# scraper silently returned 10 a position in 2026)
MIN_ROWS = {"QB": 20, "RB": 20, "WR": 20, "TE": 10}
STATUS: dict[str, dict] = {}
_LOADED = False
_ALL: list["Cached"] = []
_LOCK = threading.RLock()   # STATUS is written by page builds, ticks and requests at once


def scoring_key(ks: dict) -> str:
    """A short digest of the scoring a source rescored with: every rescored entry's cache key carries it, so a
    second league's scoring (a practice or other league's draft) never reads, or overwrites, the home league's points."""
    return hashlib.sha1(json.dumps(ks, sort_keys=True).encode()).hexdigest()[:12]


class Thin(Exception):
    pass


class Idle(Exception):
    """A valid page with nothing to project (another week shown, every game played): a success, not a failure."""


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


def status_snapshot() -> dict:
    with _LOCK:
        return {k: dict(v) for k, v in STATUS.items()}


def _load() -> None:
    """Once per process, before the first read-modify-write: fold the persisted record into STATUS so a
    restart does not overwrite other sources' history (or their failing_since)."""
    global _LOADED
    with _LOCK:
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
        db.put_state("source_status", status_snapshot())
    except Exception as e:
        log.warning("source status not saved: %s", e)


def _ok(name: str, counts: dict[str, int]) -> None:
    _load()
    with _LOCK:
        STATUS[name] = {"ok_at": _now(), "rows": counts, "error": None, "failing_since": None}
    _save()


def _failed(name: str, e: Exception) -> None:
    _load()
    with _LOCK:
        prev = STATUS.get(name) or {}
        STATUS[name] = {"ok_at": prev.get("ok_at"), "rows": prev.get("rows", {}), "error": str(e),
                        "failing_since": prev.get("failing_since") or _now()}
    log.warning("source %s failed: %s", name, e)
    _save()


class Cached:
    """Per-key TTL cache keeping the last good value through an outage. Each entry is (expires, data, ttl).
    Every good fetch is written through to the DB (`source_cache`) and the rows come back on the first read
    after a restart: inside their TTL as fresh, past it as the last good a failed refetch falls back to."""

    def __init__(self, name: str):
        self.name = name
        self._d: dict = {}
        self._restored = False
        self._restore_lock = threading.Lock()
        self._flight = Flight()  # a key's fetch in flight: page builds running at once scrape the site once
        _ALL.append(self)

    def _restore(self) -> None:
        with self._restore_lock:
            if self._restored:
                return
            self._restored = True
            try:
                rows = db.source_rows(self.name)
            except Exception as e:  # a stale schema or locked DB: run cold, as before persistence
                log.warning("source %s cache not loaded: %s", self.name, e)
                return
            now = time.time()
            for r in rows:
                exp = r["fetched_at"] + r["ttl"]
                data = {(n, pos): v for n, pos, v in r["data"]}
                self._d.setdefault(json.loads(r["key"]), (exp if exp > now else 0.0, data, r["ttl"]))

    def _store(self, key, data: dict, ttl: float) -> None:
        now = time.time()
        self._d[key] = (now + ttl, data, ttl)
        try:
            db.put_source(self.name, json.dumps(key), [[n, pos, v] for (n, pos), v in data.items()], now, ttl)
        except Exception as e:
            log.warning("source %s cache not saved: %s", self.name, e)

    def get(self, key, fetch: Callable[[], dict], min_rows: dict[str, int] | None = None,
            ttl: float | None = None) -> dict:
        if not self._restored:
            self._restore()
        hit = self._d.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        return self._flight.do(key, lambda: self._fetch(key, fetch, min_rows, ttl))

    def _fetch(self, key, fetch: Callable[[], dict], min_rows: dict[str, int] | None, ttl: float | None) -> dict:
        """Fetch `key` and install it, or fall back to its last good; every caller waiting on it gets the same."""
        hit = self._d.get(key)
        if hit and hit[0] > time.time():  # the flight before this one landed it
            return hit[1]
        ttl = TTL_S if ttl is None else ttl
        try:
            data = fetch()
            counts = check(data, min_rows)
        except Idle:
            _ok(self.name, {})
            self._store(key, {}, ttl)
            return {}
        except Exception as e:  # a parser surprise must fall back to last good too, not fail the page
            _failed(self.name, e)
            last = hit[1] if hit else {}
            self._d[key] = (time.time() + RETRY_S, last, ttl)   # cooldown: a dead site is not asked on every build
            return last
        _ok(self.name, counts)
        self._store(key, data, ttl)
        return data

    def expire(self) -> None:
        """Entries fetched at the default TTL refetch on next read. One given a longer TTL (a far week, which
        barely moves) keeps it: Refresh re-scraping FantasySharks' 14 weeks ahead took /refresh past 30 s."""
        self._d = {k: (0.0 if ttl <= TTL_S else exp, v, ttl) for k, (exp, v, ttl) in self._d.items()}


def expire_all() -> None:
    """POST /refresh: refetch on next read, keep the last good data for an outage."""
    for c in _ALL:
        c.expire()


def reset() -> None:
    """Tests only: forget every cached value and the health record in memory (a restart: the DB keeps its rows)."""
    for c in _ALL:
        c._d = {}
        c._restored = False
    global _LOADED
    with _LOCK:
        STATUS.clear()
        _LOADED = False
