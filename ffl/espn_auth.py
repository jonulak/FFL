"""Whose ESPN cookies a call goes out under (ffl-soc.3). Every user's are their own rows (`espn_credentials`, sealed by
ffl/vault.py), one per ESPN account (#460: a user may connect several, each league through the one its membership
names); the environment holds none.

- A write, and a read of what ESPN shows one team alone (its pending claims), takes the acting member's own cookies
  for that league's account, never a league-mate's (espn_write).
- A shared league read (settings, rosters, box scores, players) takes the member's own, else any other member's that
  still work (`for_read`); a public league reads with none.
- Cookies ESPN refuses (401/403) are marked failing (`refused`) and lent to nobody until the user connects that
  account again; the Reconnect banner and push are ffl-soc.6.3's.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from . import db

log = logging.getLogger(__name__)

OK_EVERY_S = 3600  # how often a passing read is written down (last_ok)


@dataclass(frozen=True)
class Cookies:
    user_id: int
    espn_s2: str = field(repr=False)
    swid: str = field(repr=False)
    cred_id: int | None = None      # the espn_credentials row: what a refusal or a pass marks
    account: str | None = None      # its swid_hash: the ESPN account

    def jar(self) -> dict:
        return {"espn_s2": self.espn_s2, "SWID": self.swid}


class NoConnection(Exception):
    """ESPN refused a league's read and no member has working cookies to make it with."""

    def __init__(self, key: str):
        super().__init__(f"Nobody in {key} has a working ESPN connection: connect ESPN to read this league")
        self.key = key


_OK: dict[int, tuple[int, float]] = {}   # credential row -> (user, when a passing read was last written, monotonic)
_OK_LOCK = threading.Lock()


def connect(user_id: int, espn_s2: str, swid: str) -> str:
    """Store (or replace) the user's cookies for that ESPN account; any League built under their old ones is dropped.
    The account (its swid_hash)."""
    espn_s2, swid = espn_s2.strip(), swid.strip()
    if not espn_s2 or not swid:
        raise ValueError("both espn_s2 and SWID are needed")
    account = db.put_credentials(user_id, espn_s2, swid)
    forget(user_id)
    return account


def disconnect(user_id: int, account: str | None = None) -> None:
    """One ESPN account's cookies (a swid_hash), or every one of the user's."""
    db.drop_credentials(user_id, account)
    forget(user_id)


def forget(user_id: int) -> None:
    """Drop what this process holds of the user's cookies: every League built with them, and the last_ok throttle."""
    from . import config
    evict_user = getattr(config.league, "evict_user", None)  # None when tests stub `league`
    if evict_user is not None:
        evict_user(user_id)
    with _OK_LOCK:
        for cid in [cid for cid, (uid, _) in _OK.items() if uid == user_id]:
            del _OK[cid]


def _row(user_id: int | None, account: str | None) -> dict | None:
    """The user's account `account`, or with None their first (a membership from before #460 names none)."""
    if user_id is None:
        return None
    rows = db.credential_rows(user_id)
    if account is None:
        return rows[0] if rows else None
    return next((r for r in rows if r["swid_hash"] == account), None)


def _working(row: dict | None) -> Cookies | None:
    if row is None or row["unreadable"] or row["failing_since"]:
        return None
    return Cookies(row["user_id"], row["espn_s2"], row["swid"], row["id"], row["swid_hash"])


def own(user_id: int | None, account: str | None = None) -> Cookies | None:
    """The user's own working cookies for `account` (None: their first account): None with no such row, one this
    ring cannot open, or one ESPN has refused."""
    return _working(_row(user_id, account))


def own_for(ref) -> Cookies | None:
    """The member's own working cookies for `ref`'s league: the ESPN account their membership names."""
    return own(ref.user_id, getattr(ref, "espn_account", None))


def accounts(user_id: int) -> list[Cookies]:
    """Every working ESPN account of the user's, first connected first."""
    return [c for c in map(_working, db.credential_rows(user_id)) if c is not None]


def failing_for(ref) -> bool:
    """Whether ESPN has refused the account `ref`'s membership names (a write is then Refused, not NotConnected)."""
    row = _row(ref.user_id, getattr(ref, "espn_account", None))
    return bool(row and row["failing_since"])


def for_read(ref, skip: frozenset[int] = frozenset()) -> Cookies | None:
    """Cookies for a shared read of `ref`'s league: the member's own, else another member's (lowest user id first),
    leaving out the users in `skip`; None when nobody's work (a public league reads without)."""
    from . import leagues
    order = [ref] + [m for m in leagues.members(ref.key) if m.user_id != ref.user_id]
    for m in order:
        if m.user_id is None or m.user_id in skip:
            continue
        if (c := own_for(m)) is not None:
            return c
    return None


def for_team(ref) -> Cookies | None:
    """Cookies for a read of what ESPN shows `ref`'s team alone (its pending claims): the member's own, else a
    co-manager's on the same team (they see it on ESPN too); never another team's member. Writes take `own_for`."""
    from . import leagues
    if (c := own_for(ref)) is not None or ref.team_id is None:
        return c
    for m in leagues.members(ref.key):
        if m.team_id == ref.team_id and m.user_id != ref.user_id and (c := own_for(m)) is not None:
            return c
    return None


def refused(c: Cookies, reason: str) -> None:
    """ESPN turned these cookies away (401/403): that account is lent to nobody and its writes refused until the user
    connects it again."""
    log.warning("ESPN refused user %s's cookies: %s", c.user_id, reason[:200])
    db.mark_credentials(c.cred_id, ok=False, reason=reason[:300])
    forget(c.user_id)


def passed(c: Cookies) -> None:
    """A read these cookies made: written down at most hourly per account."""
    now = time.monotonic()
    with _OK_LOCK:
        if now - _OK.get(c.cred_id, (c.user_id, -OK_EVERY_S))[1] < OK_EVERY_S:
            return
        _OK[c.cred_id] = (c.user_id, now)
    try:
        db.mark_credentials(c.cred_id, ok=True)
    except db.DBError as e:  # bookkeeping only: the read itself stands
        log.warning("last_ok not written for user %s: %s", c.user_id, e)


def account_status(row: dict) -> dict:
    """One account, never a value: id, connected, updated_at, last_ok, failing_since, failing_reason, unreadable."""
    return {"id": row["id"], "connected": not (row["unreadable"] or row["failing_since"]),
            "updated_at": row["updated_at"], "last_ok": row["last_ok"], "failing_since": row["failing_since"],
            "failing_reason": row["failing_reason"], "unreadable": row["unreadable"]}


def status(user_id: int, rows: list[dict] | None = None) -> dict:
    """The user's connection, never a value: `accounts` (account_status each, in `rows`' order: the user's rows,
    read now when not given) and, over them, `connected` (one works), `failing_since` (the earliest refusal of any),
    `unreadable` (one this ring can't open) and the newest `updated_at`/`last_ok`."""
    accts = [account_status(r) for r in (db.credential_rows(user_id) if rows is None else rows)]
    failing = [a["failing_since"] for a in accts if a["failing_since"]]
    return {"connected": any(a["connected"] for a in accts),
            "updated_at": max((a["updated_at"] for a in accts), default=None),
            "last_ok": max((a["last_ok"] for a in accts if a["last_ok"]), default=None),
            "failing_since": min(failing, default=None),
            "unreadable": next((a["unreadable"] for a in accts if a["unreadable"]), None),
            "accounts": accts}
