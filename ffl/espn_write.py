"""The only code that writes to ESPN: waiver claims and lineup moves on the fantasy write host, under the acting
member's own cookies (ffl-soc.3, espn_auth.own: never a league-mate's, never the environment's).

ESPN's write API is undocumented; the shapes are what its own web app sent (spike ffl-ut0.1,
tests/fixtures/espn_write). FFL_WRITES=0 swaps ESPN for an in-memory store so dev and tests exercise
the whole flow without a request.

Priority is ESPN's `subOrder`, and only a read that includes the mTeam view carries it: a bare
mPendingTransactions read returns every claim with subOrder null, in newest-first array order
(2026-10-06: three claims the app sent read back reversed). So a submit ends by numbering the claims
explicitly with ESPN's reorder call, the one its page sends when a claim is dragged.
"""
from __future__ import annotations

import itertools
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

from . import config, espn_auth


def write_url(*, ref) -> str:
    """That league's write endpoint: its season and provider id, as `config.league(ref)` reads them."""
    return (f"https://lm-api-writes.fantasy.espn.com/apis/v3/games/ffl/seasons/{ref.season}"
            f"/segments/0/leagues/{ref.provider_id}/transactions/")


# The headers ESPN's own page sends on every write.
HEADERS = {"x-fantasy-platform": "espn-fantasy-web", "x-fantasy-source": "kona"}
TIMEOUT = 15



@dataclass
class _Fake:
    """One league's in-memory ESPN under FFL_WRITES=0 (dev/tests)."""
    claims: list[dict] = field(default_factory=list)
    adds: list[dict] = field(default_factory=list)
    fail_adds: set[int] = field(default_factory=set)  # ids whose instant add "was taken"
    period: int | None = None  # ESPN's scoring period; None means "the plan week"
    last_run: str | None = None  # the last waiver run, local ISO
    slots: dict[int, str] = field(default_factory=dict)  # player id -> the slot a fake move put him in


_FAKES: dict[tuple[str, int | None], _Fake] = {}  # (league, team): a team's fake moves are its own
_IDS = itertools.count(1)  # global, so a fake claim id is unique across leagues
_READ: dict[tuple[str, int | None], tuple[float, dict]] = {}  # (league, team) -> its last pending read and when (monotonic)
_GEN = 0  # bumped by every write, so a read in flight across one is not kept
_GEN_LOCK = threading.Lock()  # a claim save and a lineup move can write at once


EXPIRED = "ESPN refused your login: reconnect ESPN"
UNREACHABLE = "ESPN couldn't be reached, try again"


class WriteError(Exception):
    """`status` is ESPN's HTTP status, or 0 when ESPN gave no usable answer (network, timeout, an unreadable body)."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"{UNREACHABLE} ({detail})" if status == 0 else f"ESPN {status}: {detail}")
        self.status, self.detail = status, detail

    @property
    def expired(self) -> bool:
        return self.status in (401, 403)

    @property
    def message(self) -> str:
        """What the page shows: only a real 401/403 says the login expired."""
        return EXPIRED if self.expired else UNREACHABLE if self.status == 0 else str(self)


class NotConnected(WriteError):
    """A write refused before anything reached ESPN: the member has no ESPN connection of their own (ffl-soc.6.3)."""

    def __init__(self):
        super().__init__(409, "not connected")

    @property
    def message(self) -> str:
        return "ESPN moves need your own ESPN connection, which isn't set up yet"


class Refused(WriteError):
    """A write refused before anything reached ESPN: ESPN has refused the member's cookies since their last connect."""

    def __init__(self):
        super().__init__(401, "cookies refused earlier")


def _own(ref) -> espn_auth.Cookies:
    """The acting member's own cookies, or the write is refused here: NotConnected with none, Refused when ESPN has
    turned theirs away."""
    c = espn_auth.own(ref.user_id)
    if c is not None:
        return c
    st = espn_auth.status(ref.user_id) if ref.user_id is not None else {"failing_since": None}
    raise Refused() if st["failing_since"] else NotConnected()


def _writes() -> bool:
    # Read at call time so a test can flip it with monkeypatch.setenv.
    return os.environ.get("FFL_WRITES", "1") != "0"


def _fake(ref) -> _Fake:
    """That team's fake store in its league (co-managers share it, as ESPN would), made on first use."""
    return _FAKES.setdefault((ref.key, ref.team_id), _Fake())


def reset_fakes() -> None:
    """Empty every league's fake store and kept pending read (tests)."""
    _FAKES.clear()
    with _GEN_LOCK:
        _READ.clear()


def writes() -> bool:
    """False under FFL_WRITES=0: every write goes to the in-memory fakes, and the UI must say so."""
    return _writes()


def fake_slots(*, ref) -> dict[int, str]:
    return {} if _writes() else dict(_fake(ref).slots)


def _team_id(*, ref) -> int:
    from .espn_season import fresh_league, my_team_id
    return my_team_id(fresh_league(ref), ref)


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
    # Not captured from ESPN's site (ffl-ut0.7): the body github.com/ryanjadhav/espn-fantasy sends (cmd_freeagent),
    # which agrees with ESPN's own record of an executed add (tests/fixtures/espn_write/add_executed_record.json).
    # A claim's body without bidAmount, which that tool never sends.
    body = {**submit_body(add, drop, week, team_id, member_id), "type": "FREEAGENT"}
    del body["bidAmount"]
    return body


def parse_pending(doc: dict, team_id: int) -> list[dict]:
    """My pending claims in priority order. `priority` is ESPN's subOrder, None when the read did not carry it
    (the order is then unknown, and an unnumbered claim sorts after the numbered ones)."""
    rows = [t for t in doc.get("pendingTransactions", []) if t.get("teamId") == team_id
            and t.get("type") == "WAIVER" and t.get("status") == "PENDING"]
    # subOrder is the priority; the array order is not.
    rows.sort(key=lambda t: (t.get("subOrder") is None, t.get("subOrder") or 0))
    out = []
    for t in rows:
        add = next((i["playerId"] for i in t.get("items") or [] if i.get("type") == "ADD"), None)
        if add is None:  # malformed (no ADD item): not a claim this app can match, so skip it
            continue
        drop = next((i["playerId"] for i in t["items"] if i.get("type") == "DROP"), None)
        out.append({"id": str(t["id"]), "add": int(add), "drop": None if drop is None else int(drop),
                    "priority": t.get("subOrder")})
    return out


def reorder_body(ids: list[str]) -> list[dict]:
    """ESPN's reorder call: every pending claim's id with its new priority, 1 first."""
    return [{"id": i, "subOrder": n} for n, i in enumerate(ids, 1)]


# ESPN's lineupSlotId by slot name (espn-api's POSITION_MAP inverted; WR=4 and FLEX=23 seen in the 2026-10-07 capture).
SLOT_ID = {"QB": 0, "RB": 2, "RB/WR": 3, "WR": 4, "WR/TE": 5, "TE": 6, "OP": 7, "D/ST": 16, "K": 17,
           "BE": 20, "IR": 21, "RB/WR/TE": 23}


def lineup_body(items: list[tuple[int, str, str]], week: int, team_id: int, member_id: str) -> dict:
    """ESPN's lineup move (captured off its team page, tests/fixtures/espn_write/lineup_request.json): one
    ROSTER transaction with a LINEUP item for every player whose slot changes."""
    return {"isLeagueManager": False, "teamId": team_id, "type": "ROSTER", "memberId": member_id,
            "scoringPeriodId": week, "executionType": "EXECUTE",
            "items": [{"playerId": p, "type": "LINEUP", "fromLineupSlotId": SLOT_ID[f], "toLineupSlotId": SLOT_ID[t]}
                      for p, f, t in items]}


def move_lineup(items: list[tuple[int, str, str]], week: int, *, ref) -> None:
    """Move players between slots in one transaction; raises WriteError unless ESPN executed it."""
    if not _writes():
        _fake(ref).slots.update({p: t for p, _, t in items})
        return
    c = _own(ref)
    res = _post(lineup_body(items, week, _team_id(ref=ref), c.swid), cookies=c, ref=ref)
    if not isinstance(res, dict) or res.get("status") != "EXECUTED":
        status = res.get("status") if isinstance(res, dict) else None
        raise WriteError(200, f"lineup move not executed (status {status!r})")


def _post(body: dict | list, url: str | None = None, *, cookies: espn_auth.Cookies, ref) -> dict | list:
    """POST under the member's own `cookies` (`_own`); a 401/403 marks them refused."""
    global _GEN
    with _GEN_LOCK:  # before and whatever the answer: a write that errored may still have landed (any league's read goes)
        _READ.clear()
        _GEN += 1
    r = requests.post(url or write_url(ref=ref), json=body, headers=HEADERS, cookies=cookies.jar(), timeout=TIMEOUT)
    if r.status_code >= 400:
        if r.status_code in (401, 403):
            espn_auth.refused(cookies.user_id, f"ESPN {r.status_code} on a write")
        raise WriteError(r.status_code, r.text[:500])
    return r.json()


def _read_error(e: Exception) -> WriteError:
    """A failed ESPN read as a WriteError. espn-api raises ESPNAccessDenied for a 401 (after trying its other
    endpoint), ESPNInvalidLeague for a 404 and ESPNUnknownError("ESPN returned an HTTP <n>") for any other status,
    403 included; anything else (timeout, connection, an unreadable body) had no HTTP status to blame, so it is
    "couldn't reach ESPN", never "login expired"."""
    from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague, ESPNUnknownError
    if isinstance(e, ESPNAccessDenied):
        return WriteError(401, str(e))
    if isinstance(e, ESPNInvalidLeague):
        return WriteError(404, str(e))
    if isinstance(e, ESPNUnknownError) and (m := re.search(r"HTTP (\d+)", str(e))):
        return WriteError(int(m.group(1)), str(e))
    status = getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(status, int) and status >= 400:
        return WriteError(status, str(e))
    return WriteError(0, f"{type(e).__name__}: {e}"[:300])


def _pending_doc(max_age: float, *, ref) -> dict:
    """The mPendingTransactions + mTeam document, which also carries mStatus's waiverLastExecutionDate. A caller
    passing max_age takes the last read when it is younger than that (the Claims page: each GET and save paid two
    ESPN reads, 1.5-2.3 s); it is kept per (league, team), any write drops every one, and a read that spans a write
    is not kept. ESPN shows a team's pending claims to that team alone, so it is read under the team's own cookies
    (the member's, else a co-manager's: `espn_auth.for_team`), never another team's."""
    key = (ref.key, ref.team_id)
    kept = _READ.get(key)
    if max_age and kept and time.monotonic() - kept[0] < max_age:
        return kept[1]
    gen = _GEN
    c = espn_auth.for_team(ref)
    if c is None:
        _own(ref)  # raises the member's own reason: NotConnected, or Refused
        raise NotConnected()
    try:
        # mTeam is what makes ESPN include subOrder (the priority) on each pending claim
        doc = config.raw_league_get(["mPendingTransactions", "mTeam"], cookies=c, ref=ref)
    except Exception as e:  # noqa: BLE001 — every read failure is a WriteError, sorted by _read_error
        err = _read_error(e)
        if err.expired:
            espn_auth.refused(c.user_id, f"ESPN {err.status} on the pending read")
        raise err from e
    with _GEN_LOCK:
        if gen == _GEN:
            _READ[key] = (time.monotonic(), doc)
    return doc


def pending_state(max_age: float = 0, *, ref) -> tuple[list[dict], int | None]:
    """My pending waiver claims in ESPN's processing order, and ESPN's current scoring period (the week its write
    bodies want), from one mPendingTransactions read. The period is None when the document has none."""
    if not _writes():
        f = _fake(ref)
        return [{**c, "priority": n} for n, c in enumerate(f.claims, 1)], f.period
    doc = _pending_doc(max_age, ref=ref)
    team = _team_id(ref=ref)
    try:
        period = doc.get("scoringPeriodId")
        return parse_pending(doc, team), None if period is None else int(period)
    except (AttributeError, KeyError, TypeError, ValueError) as e:  # an answer this code cannot read (a row with no id)
        raise WriteError(0, f"unreadable pending claims ({type(e).__name__}: {e})"[:300]) from e


def last_waiver_run(max_age: float = 0, *, ref) -> str | None:
    """When ESPN last ran waivers (waiverLastExecutionDate, off the pending read), local ISO to the second; None
    when the read fails or carries no date, so a plan is never retired on a guess."""
    if not _writes():
        return _fake(ref).last_run
    try:
        ms = _pending_doc(max_age, ref=ref).get("status", {}).get("waiverLastExecutionDate")
    except Exception:  # noqa: BLE001
        return None
    return None if ms is None else datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")


def pending_claims(*, ref) -> list[dict]:
    """My pending waiver claims in ESPN's processing order."""
    return pending_state(ref=ref)[0]


def submit_claim(add: int, drop: int | None, week: int, *, ref) -> str:
    """Post one claim (appended last in priority); returns ESPN's claim id."""
    if not _writes():
        cid = f"fake-{next(_IDS)}"
        _fake(ref).claims.append({"id": cid, "add": add, "drop": drop})
        return cid
    c = _own(ref)
    res = _post(submit_body(add, drop, week, _team_id(ref=ref), c.swid), cookies=c, ref=ref)
    if not isinstance(res, dict) or res.get("id") is None:  # ESPN may hold it anyway: the read-back shows it
        raise WriteError(200, "claim sent but ESPN's answer carries no id")
    return str(res["id"])


def reorder_claims(ids: list[str], *, ref) -> None:
    """Number my pending claims 1..n in this order (every pending id, outside claims included)."""
    if not _writes():
        rank = {i: n for n, i in enumerate(ids)}
        _fake(ref).claims.sort(key=lambda c: rank.get(c["id"], len(rank)))
        return
    c = _own(ref)
    team = _team_id(ref=ref)
    _post(reorder_body(ids), url=write_url(ref=ref).replace("/transactions/", f"/teams/{team}/pendingTransactions"),
          cookies=c, ref=ref)


def cancel_claim(claim_id: str, week: int, *, ref) -> None:
    if not _writes():
        f = _fake(ref)
        f.claims[:] = [c for c in f.claims if c["id"] != claim_id]
        return
    c = _own(ref)
    _post(cancel_body(claim_id, week, _team_id(ref=ref), c.swid), cookies=c, ref=ref)


def add_free_agent(add: int, drop: int | None, week: int, *, ref) -> None:
    """Instant add; raises WriteError when ESPN refuses (taken, locked, ...)."""
    if not _writes():
        f = _fake(ref)
        if add in f.fail_adds:
            raise WriteError(409, f"player {add} is no longer a free agent")
        f.adds.append({"add": add, "drop": drop})
        return
    c = _own(ref)
    res = _post(add_body(add, drop, week, _team_id(ref=ref), c.swid), cookies=c, ref=ref)
    # A 200 is not a landed add: only "EXECUTED" is (a guess, like add_body; ffl-ut0.7 pins the real shape and value).
    if not isinstance(res, dict) or res.get("status") != "EXECUTED":
        status = res.get("status") if isinstance(res, dict) else None
        raise WriteError(200, f"instant add not executed (status {status!r})")
