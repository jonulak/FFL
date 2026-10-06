"""The only code that writes to ESPN: waiver claims on the fantasy write host, with the .env cookies.

ESPN's write API is undocumented; the shapes are what its own web app sent (spike ffl-ut0.1,
tests/fixtures/espn_write). FFL_WRITES=0 swaps ESPN for an in-memory store so dev and tests exercise
the whole flow without a request.

There is no reorder call: a claim is appended at the end of the priority order, so submitting in the
order wanted (clearing and reposting if it must change) is the order ESPN processes them in.
"""
from __future__ import annotations

import itertools
import os

import requests

from . import config

WRITE_URL = (f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/{config.SEASON}"
             f"/segments/0/leagues/{config.LEAGUE_ID}/transactions/")
# The headers ESPN's own page sends on every write.
HEADERS = {"x-fantasy-platform": "espn-fantasy-web", "x-fantasy-source": "kona"}
TIMEOUT = 15

_FAKE: list[dict] = []
_FAKE_ADDS: list[dict] = []
_FAIL_ADDS: set[int] = set()  # dev/tests under FFL_WRITES=0: ids whose instant add "was taken"
_FAKE_PERIOD: int | None = None  # dev/tests under FFL_WRITES=0: ESPN's scoring period; None means "the plan week"
_IDS = itertools.count(1)


class WriteError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"ESPN {status}: {detail}")
        self.status, self.detail = status, detail

    @property
    def expired(self) -> bool:
        return self.status in (401, 403)


def _writes() -> bool:
    # Read at call time so a test can flip it with monkeypatch.setenv.
    return os.environ.get("FFL_WRITES", "1") != "0"


def _team_id() -> int:
    from .espn_season import fresh_league, my_team_id
    return my_team_id(fresh_league())


def submit_body(add: int, drop: int | None, week: int, team_id: int, member_id: str) -> dict:
    items = [{"playerId": add, "type": "ADD", "toTeamId": team_id}]
    if drop is not None:
        items.append({"playerId": drop, "type": "DROP", "fromTeamId": team_id})
    return {"isLeagueManager": False, "teamId": team_id, "type": "WAIVER", "memberId": member_id,
            "scoringPeriodId": week, "executionType": "EXECUTE", "items": items, "bidAmount": None}


def cancel_body(claim_id: str, week: int, team_id: int, member_id: str) -> dict:
    return {"isLeagueManager": False, "teamId": team_id, "type": "WAIVER", "memberId": member_id,
            "scoringPeriodId": week, "executionType": "CANCEL", "relatedTransactionId": claim_id}


def add_body(add: int, drop: int | None, week: int, team_id: int, member_id: str) -> dict:
    # FREEAGENT shape not captured yet (instant, real move): bead ffl-ut0.7 replaces this guess.
    return {**submit_body(add, drop, week, team_id, member_id), "type": "FREEAGENT"}


def parse_pending(doc: dict, team_id: int) -> list[dict]:
    rows = [t for t in doc.get("pendingTransactions", []) if t.get("teamId") == team_id
            and t.get("type") == "WAIVER" and t.get("status") == "PENDING"]
    # subOrder is the priority; the array order is not.
    rows.sort(key=lambda t: t.get("subOrder") or 0)
    out = []
    for t in rows:
        add = next((i["playerId"] for i in t.get("items") or [] if i.get("type") == "ADD"), None)
        if add is None:  # malformed (no ADD item): not a claim this app can match, so skip it
            continue
        drop = next((i["playerId"] for i in t["items"] if i.get("type") == "DROP"), None)
        out.append({"id": str(t["id"]), "add": int(add), "drop": None if drop is None else int(drop)})
    return out


def _post(body: dict) -> dict:
    r = requests.post(WRITE_URL, json=body, headers=HEADERS, cookies=config.cookies(), timeout=TIMEOUT)
    if r.status_code >= 400:
        raise WriteError(r.status_code, r.text[:500])
    return r.json()


def pending_state() -> tuple[list[dict], int | None]:
    """My pending waiver claims in ESPN's processing order, and ESPN's current scoring period (the week its write
    bodies want), from one mPendingTransactions read. The period is None when the document has none."""
    if not _writes():
        return [dict(c) for c in _FAKE], _FAKE_PERIOD
    try:
        doc = config.raw_league_get("mPendingTransactions")
    except Exception as e:  # espn-api raises its own types for 401/403
        raise WriteError(getattr(getattr(e, "response", None), "status_code", 0) or 401, str(e))
    period = doc.get("scoringPeriodId")
    return parse_pending(doc, _team_id()), None if period is None else int(period)


def pending_claims() -> list[dict]:
    """My pending waiver claims in ESPN's processing order."""
    return pending_state()[0]


def submit_claim(add: int, drop: int | None, week: int) -> str:
    """Post one claim (appended last in priority); returns ESPN's claim id."""
    if not _writes():
        cid = f"fake-{next(_IDS)}"
        _FAKE.append({"id": cid, "add": add, "drop": drop})
        return cid
    res = _post(submit_body(add, drop, week, _team_id(), config.SWID))
    return str(res["id"])


def cancel_claim(claim_id: str, week: int) -> None:
    if not _writes():
        _FAKE[:] = [c for c in _FAKE if c["id"] != claim_id]
        return
    _post(cancel_body(claim_id, week, _team_id(), config.SWID))


def add_free_agent(add: int, drop: int | None, week: int) -> None:
    """Instant add; raises WriteError when ESPN refuses (taken, locked, ...)."""
    if not _writes():
        if add in _FAIL_ADDS:
            raise WriteError(409, f"player {add} is no longer a free agent")
        _FAKE_ADDS.append({"add": add, "drop": drop})
        return
    res = _post(add_body(add, drop, week, _team_id(), config.SWID))
    # A 200 is not a landed add: only "EXECUTED" is (a guess, like add_body; ffl-ut0.7 pins the real shape and value).
    if not isinstance(res, dict) or res.get("status") != "EXECUTED":
        status = res.get("status") if isinstance(res, dict) else None
        raise WriteError(200, f"instant add not executed (status {status!r})")
