"""Whose ESPN cookies a call goes out under (ffl-soc.3). Every user's are their own row (`espn_credentials`, sealed by
ffl/vault.py); the environment holds none.

- A write, and a read of what ESPN shows one team alone (its pending claims), takes the acting member's own cookies,
  never a league-mate's (espn_write).
- A shared league read (settings, rosters, box scores, players) takes the member's own, else any other member's that
  still work (`for_read`); a public league reads with none.
- Cookies ESPN refuses (401/403) are marked failing (`refused`) and lent to nobody until the user connects again; the
  Reconnect banner and push are ffl-soc.6.3's.
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

    def jar(self) -> dict:
        return {"espn_s2": self.espn_s2, "SWID": self.swid}


class NoConnection(Exception):
    """ESPN refused a league's read and no member has working cookies to make it with."""

    def __init__(self, key: str):
        super().__init__(f"Nobody in {key} has a working ESPN connection: connect ESPN to read this league")
        self.key = key


_OK: dict[int, float] = {}   # user id -> when a passing read was last written (monotonic)
_OK_LOCK = threading.Lock()


def connect(user_id: int, espn_s2: str, swid: str) -> None:
    """Store (or replace) the user's cookies; any League built under their old ones is dropped."""
    espn_s2, swid = espn_s2.strip(), swid.strip()
    if not espn_s2 or not swid:
        raise ValueError("both espn_s2 and SWID are needed")
    db.put_credentials(user_id, espn_s2, swid)
    forget(user_id)


def disconnect(user_id: int) -> None:
    db.drop_credentials(user_id)
    forget(user_id)


def forget(user_id: int) -> None:
    """Drop what this process holds of the user's cookies: every League built with them, and the last_ok throttle."""
    from . import config
    evict_user = getattr(config.league, "evict_user", None)  # None when tests stub `league`
    if evict_user is not None:
        evict_user(user_id)
    with _OK_LOCK:
        _OK.pop(user_id, None)


def own(user_id: int | None) -> Cookies | None:
    """The user's own working cookies: None with no row, one this ring cannot open, or one ESPN has refused."""
    if user_id is None:
        return None
    row = db.credential_row(user_id)
    if row is None or row["unreadable"] or row["failing_since"]:
        return None
    return Cookies(user_id, row["espn_s2"], row["swid"])


def for_read(ref, skip: frozenset[int] = frozenset()) -> Cookies | None:
    """Cookies for a shared read of `ref`'s league: the member's own, else another member's (lowest user id first),
    leaving out `skip`; None when nobody's work (a public league reads without)."""
    from . import leagues
    order = [ref.user_id] + [m.user_id for m in leagues.members(ref.key) if m.user_id != ref.user_id]
    for uid in order:
        if uid is None or uid in skip:
            continue
        if (c := own(uid)) is not None:
            return c
    return None


def for_team(ref) -> Cookies | None:
    """Cookies for a read of what ESPN shows `ref`'s team alone (its pending claims): the member's own, else a
    co-manager's on the same team (they see it on ESPN too); never another team's member. Writes take `own` only."""
    from . import leagues
    if (c := own(ref.user_id)) is not None or ref.team_id is None:
        return c
    for m in leagues.members(ref.key):
        if m.team_id == ref.team_id and m.user_id != ref.user_id and (c := own(m.user_id)) is not None:
            return c
    return None


def refused(user_id: int, reason: str) -> None:
    """ESPN turned the user's cookies away (401/403): lent to nobody, their writes refused, until they connect again."""
    log.warning("ESPN refused user %s's cookies: %s", user_id, reason[:200])
    db.mark_credentials(user_id, ok=False, reason=reason[:300])
    forget(user_id)


def passed(c: Cookies) -> None:
    """A read these cookies made: written down at most hourly per user."""
    now = time.monotonic()
    with _OK_LOCK:
        if now - _OK.get(c.user_id, -OK_EVERY_S) < OK_EVERY_S:
            return
        _OK[c.user_id] = now
    try:
        db.mark_credentials(c.user_id, ok=True)
    except db.DBError as e:  # bookkeeping only: the read itself stands
        log.warning("last_ok not written for user %s: %s", c.user_id, e)


def status(user_id: int) -> dict:
    """What the user's connection is, never a value: connected, updated_at, last_ok, failing_since, unreadable."""
    row = db.credential_row(user_id)
    if row is None:
        return {"connected": False, "updated_at": None, "last_ok": None, "failing_since": None, "unreadable": None}
    return {"connected": not (row["unreadable"] or row["failing_since"]), "updated_at": row["updated_at"],
            "last_ok": row["last_ok"], "failing_since": row["failing_since"], "unreadable": row["unreadable"]}
