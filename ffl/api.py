"""FastAPI glue for the season UI: ESPN/Sleeper state behind a short cache, two writes.

    uv run uvicorn ffl.api:app --host 127.0.0.1 --port 8300

Loopback only; Next.js on :3300 proxies /api/* here. Stale payloads are kept past the TTL and
served, flagged, when a source is down — never a blank page on Sunday.

Only two endpoints write anything: POST /decisions appends to journal/DECISIONS.md, and
POST /refresh runs the journal sync. Every GET is read-only.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, field_validator

from . import espn_season, season
from .config import JOURNAL
from .journal import sync_results, sync_transactions
from .settings import load

TTL = 120.0
DECISIONS = JOURNAL / "DECISIONS.md"


class Cache:
    """TTL cache that keeps expired entries so a dead source can still be answered."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._d: dict[str, tuple[float, float, object]] = {}  # key -> (expires, built_at, payload)

    def get_or_build(self, key: str, fn: Callable[[], object], ttl: float | None = None):
        now = time.time()
        hit = self._d.get(key)
        if hit and hit[0] > now:
            return hit[2], None
        try:
            payload = fn()
        except Exception as e:  # noqa: BLE001 — any source failure falls back to stale
            if hit:
                return hit[2], datetime.fromtimestamp(hit[1]).strftime("%H:%M")
            src = "sleeper" if "sleeper" in repr(e).lower() else "espn"
            raise HTTPException(status_code=502, detail={"source": src, "error": str(e)})
        self._d[key] = (now + (ttl or self.ttl), now, payload)
        return payload, None

    def expire_all(self):
        self._d = {k: (0.0, b, p) for k, (_, b, p) in self._d.items()}

    def clear(self):
        self._d.clear()


cache = Cache(TTL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The journal is the durable record: the API reads and appends to it, and never creates it.
    # Booting without it would mean a 500 on /journal and a silent new file on the first
    # decision, so refuse to start instead. Read the module global so tests can repoint it.
    if not DECISIONS.exists():
        raise RuntimeError(f"journal decisions log missing: {DECISIONS} (the API never creates it)")
    yield


app = FastAPI(title="FFL season API", lifespan=lifespan)


def _journal_serve(fn: Callable[[], dict]) -> dict:
    """Journal reads/writes are local files, but a missing or unreadable one must still come back
    in the shape the UI's error card expects, never as a bare 500 traceback."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — any journal failure is reported like a source failure
        raise HTTPException(status_code=502, detail={"source": "journal", "error": str(e)})


def _serve(key: str, fn: Callable[[], dict]) -> dict:
    payload, stale = cache.get_or_build(key, fn)
    return {**payload, "stale_since": stale}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/week")
def week_current():
    return _serve("week:current", lambda: espn_season.week_state(None))


@app.get("/week/{n}")
def week(n: int):
    return _serve(f"week:{n}", lambda: espn_season.week_state(n))


@app.get("/waivers")
def waivers(pos: Literal["QB", "RB", "WR", "TE", "D/ST", "K"] = Query(...)):
    data = _serve("waivers", lambda: espn_season.free_agent_pool(None))
    return {"week": data["week"], "pos": pos, "rows": data["positions"].get(pos, []),
            "stale_since": data["stale_since"]}


@app.get("/league")
def league_():
    return _serve("league", espn_season.league_state)


@app.get("/journal")
def journal_():
    return {**_journal_serve(espn_season.journal_state), "stale_since": None}


@app.get("/draft")
def draft_():
    return {**espn_season.draft_recap(), "stale_since": None}


class Decision(BaseModel):
    title: str
    decision: str
    reasoning: str
    alternatives: str = ""

    @field_validator("title", "decision", "reasoning")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("required")
        return v


@app.post("/decisions")
def post_decision(d: Decision):
    text = _journal_serve(
        lambda: season.append_decision(DECISIONS, d.title, d.decision, d.reasoning, d.alternatives))
    return {"decisions_md": text}


def sync_all() -> dict:
    # espn_api caches current_week and the records in League.__init__; a months-old League would
    # make sync_results rewrite journal/results/weekly.csv truncated to the boot week.
    espn_season.refresh_league()
    lg = espn_season.fresh_league()
    settings = load()
    sync_transactions(lg)
    sync_results(lg, settings)
    return {"transactions": True, "results": True}


@app.post("/refresh")
def refresh():
    # expire_all, not clear: a refresh while ESPN is down must still leave the stale payloads
    # behind for the fallback to serve, flagged — never a blank page on Sunday.
    cache.expire_all()
    # the Sleeper-week, season-projection and injury caches live in espn_season; Refresh busts those too
    espn_season._SL.clear()
    espn_season._SEASON_CACHE.clear()
    if espn_season._INJ:  # expire, not clear: _INJ is the last-good store the outage fallback serves
        espn_season._INJ[0] = 0.0
    try:
        synced = sync_all()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — an ESPN failure is a 502, not a bare traceback
        raise HTTPException(status_code=502, detail={"source": "espn", "error": str(e)})
    return {"synced": synced, "at": datetime.now().strftime("%H:%M")}
