"""Push notifications for season events (bead ffl-e9x; design in its DESIGN field).

Detectors turn what the API already sees into notices; each is queued in the DB's `notices` outbox under a key that
names the event, so an event is one notice however many times it is seen, across restarts. The flush (every
`POST /notify/tick`, launchd/com.jonulak.ffl-notify) sends the queue by Web Push outside quiet hours. Pure functions
take parsed inputs and `at`; the I/O is at the bottom. FFL_NOTIFY=0 sends nothing.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta

from . import db
from .season import slot_label

log = logging.getLogger(__name__)

ADDS = {"FA ADDED", "WAIVER ADDED"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def move_notices(rows: list[dict], at: str) -> list[dict]:
    """One notice per new transaction row. A trade's sent side is skipped: its received row names the same player."""
    out = []
    for r in rows:
        action = r["action"]
        if action == "TRADE_SENT":
            continue
        if action.startswith("TRADE"):
            kind, body = "trade", f"{r['team']} get {r['player']}"
        else:
            kind, body = "move", f"{r['team']} {'+' if action in ADDS else '−'}{r['player']}"
        out.append({"key": f"move:{r['id']}", "kind": kind, "title": "League move", "body": body,
                    "url": "/transactions", "created_at": at})
    return out


def enqueue_moves(rows: list[dict]) -> int:
    """Queue the new log rows. Never fails the sync that found them: a DB error is logged."""
    try:
        return db.add_notices(move_notices(rows, now()))
    except db.DBError as e:
        log.warning("notify: moves not queued: %s", e)
        return 0


# The statuses that mean a starter will not (or probably will not) play: Questionable stays the call's business.
INJURY_LABEL = {"OUT": "OUT", "DOUBTFUL": "Doubtful", "INJURY_RESERVE": "IR"}
NOT_STARTING = {"BE", "IR"}


def injury_notices(flags: dict[int, dict], week: int, at: str) -> list[dict]:
    """A notice for each starter of mine (a lineup slot, not bench or IR) whose game has not locked and whose status
    is OUT, Doubtful or IR. Keyed by week and status: a status seen again is the same notice, a new one is another."""
    out = []
    for pid, f in flags.items():
        status, slot = f.get("injury"), f.get("slot")
        if status not in INJURY_LABEL or not slot or slot in NOT_STARTING or f.get("locked"):
            continue
        out.append({"key": f"injury:{week}:{pid}:{status}", "kind": "injury",
                    "title": f"{f.get('name') or f'Player {pid}'} {INJURY_LABEL[status]}",
                    "body": f"Starting at {slot_label(slot)}. Tap to make the call.", "url": "/roster", "created_at": at})
    return out


def waiver_notices(run: str | None, prev_run: str | None, pending: list[dict], my_adds: set[int],
                   names: dict[int, str], at: str, still_pending: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """One notice when ESPN's waiver run moved past the one last seen: the claims pending before it, won (my team added
    the player) or lost. A claim whose id is still pending (this league's waivers clear on more than one day) was not
    processed by that run: neither won nor lost, and not counted. Silent the first time a run is seen (nothing to
    compare), with no claims pending, and when none of them resolved."""
    if run is None or prev_run is None or run == prev_run:
        return []
    pending = [c for c in pending if c["id"] not in still_pending]
    if not pending:
        return []

    def name(pid: int) -> str:
        return names.get(pid) or f"player {pid}"

    won = [c["add"] for c in pending if c["add"] in my_adds]
    lost = [c["add"] for c in pending if c["add"] not in my_adds]
    parts = ([f"Won {', '.join(map(name, won))}."] if won else []) + ([f"Lost {', '.join(map(name, lost))}."] if lost else [])
    return [{"key": f"waivers:{run}", "kind": "waivers", "title": f"Waivers: {len(won)} of {len(pending)} claims won",
             "body": " ".join(parts), "url": "/transactions", "created_at": at}]


def source_notices(status: dict, t: datetime, at: str, hours: int = 24) -> list[dict]:
    """One notice per failing spell of a projection source (keyed by when it began) once it passes
    `hours`: the consensus has been running without it."""
    out = []
    for name, s in sorted(status.items()):
        since = s.get("failing_since")
        if not since or t - datetime.fromisoformat(since) < timedelta(hours=hours):
            continue
        out.append({"key": f"source:{name}:{since}", "kind": "source", "title": f"{name} projections failing",
                    "body": f"Since {since[:16].replace('T', ' ')}: {s.get('error')}. Projections run on the other sources.",
                    "url": "/players", "created_at": at})
    return out


def check_sources() -> int:
    return db.add_notices(source_notices(db.get_state("source_status", {}), datetime.now().astimezone(), now()))


DEFAULT_PREFS = {"move": True, "injury": True, "waivers": True, "quiet": ["23:00", "07:00"]}
MAX_AGE = timedelta(hours=24)
BODY_MAX = 180
_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def prefs() -> dict:
    return {**DEFAULT_PREFS, **db.get_state("prefs", {})}


def put_prefs(p: dict) -> dict:
    merged = {**prefs(), **{k: v for k, v in p.items() if k in DEFAULT_PREFS}}
    q = merged["quiet"]
    if not (isinstance(q, list) and len(q) == 2 and all(isinstance(s, str) and _HM.match(s) for s in q)):
        raise ValueError(f"quiet must be two HH:MM times, got {q!r}")
    for k in ("move", "injury", "waivers"):
        if not isinstance(merged[k], bool):
            raise ValueError(f"{k} must be true or false, got {merged[k]!r}")
    db.put_state("prefs", merged)
    return merged


def in_quiet(t: datetime, quiet: list[str]) -> bool:
    """Inside [start, end) local; a window across midnight wraps; start == end is no quiet hours."""
    start, end, hm = quiet[0], quiet[1], t.strftime("%H:%M")
    if start == end:
        return False
    return start <= hm < end if start < end else hm >= start or hm < end


def _join(bodies: list[str], weights: list[int] | None = None) -> str:
    """'; '-joined, cut to BODY_MAX with '… +N more' naming what was left out (N counts `weights`, default one each)."""
    w = weights or [1] * len(bodies)
    for n in range(len(bodies), 0, -1):
        rest = sum(w[n:])
        s = "; ".join(bodies[:n]) + (f"… +{rest} more" if rest else "")
        if len(s) <= BODY_MAX:
            return s
    if len(bodies) == 1:
        return _cap(bodies[0])
    return bodies[0][:BODY_MAX - 12] + f"… +{sum(w[1:])} more"


_MOVE = re.compile(r"^(.+?) ([+−].+)$")


def _by_team(bodies: list[str]) -> tuple[list[str], list[int]]:
    """'Bears +A' and 'Bears −B' become one 'Bears +A −B' fragment, teams in first-seen order, with each fragment's row
    count. A body that is not '<team> <+|−><player>' stays a fragment of its own."""
    frags: dict[str, list[str]] = {}
    for i, b in enumerate(bodies):
        m = _MOVE.match(b)
        team, signed = (m[1], m[2]) if m else (f"\0{i}", b)
        frags.setdefault(team, []).append(signed)
    return ([v[0] if k.startswith("\0") else f"{k} {' '.join(v)}" for k, v in frags.items()],
            [len(v) for v in frags.values()])


def _cap(s: str) -> str:
    return s if len(s) <= BODY_MAX else s[:BODY_MAX - 1] + "…"


def _age_over(n: dict, t: datetime) -> bool:
    """Older than MAX_AGE; a created_at that does not parse or has no offset counts as stale, never an error."""
    try:
        return t - datetime.fromisoformat(n["created_at"]) > MAX_AGE
    except (ValueError, TypeError, KeyError):
        return True


def plan(unsent: list[dict], prefs: dict, t: datetime) -> tuple[list[dict], dict[str, list[str]]]:
    """The pushes this flush sends, and the notices it only marks (switched off, or older than MAX_AGE)."""
    marks: dict[str, list[str]] = {"off": [], "expired": []}
    live = []
    for n in unsent:
        if _age_over(n, t):
            marks["expired"].append(n["key"])
        elif not prefs.get("move" if n["kind"] == "trade" else n["kind"], True):
            marks["off"].append(n["key"])
        else:
            live.append(n)
    pushes = []
    moves = [n for n in live if n["kind"] == "move"]
    if moves:
        pushes.append({"keys": [n["key"] for n in moves], "title": f"{len(moves)} league move{'s' if len(moves) > 1 else ''}",
                       "body": _join(*_by_team([n["body"] for n in moves])), "url": "/transactions", "tag": "moves"})
    trades = [n for n in live if n["kind"] == "trade"]
    if trades:
        pushes.append({"keys": [n["key"] for n in trades], "title": "Trade",
                       "body": _join([n["body"] for n in trades]), "url": "/transactions", "tag": "trade"})
    for n in live:
        if n["kind"] not in ("move", "trade"):
            pushes.append({"keys": [n["key"]], "title": n["title"], "body": _cap(n["body"]), "url": n["url"], "tag": n["key"]})
    return pushes, marks


def deliver(push: dict, subs: list[dict], send) -> tuple[bool, list[str]]:
    """Send to every device. Accepted when any device took it; a 404/410 names a dead subscription."""
    payload = {k: push[k] for k in ("title", "body", "url", "tag")}
    ok, dead = False, []
    for sub in subs:
        try:
            send(sub, payload)
            ok = True
        except Exception as e:  # noqa: BLE001 — pywebpush raises WebPushException; anything else is the network
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):
                dead.append(sub["endpoint"])
            else:
                log.warning("notify: push to %s failed: %s", sub["endpoint"][:40], e)
    return ok, dead


def webpush_sender(sub: dict, payload: dict) -> None:
    from pywebpush import webpush
    webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]}, data=json.dumps(payload),
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
            vapid_claims={"sub": os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")}, ttl=24 * 3600, timeout=10)


def sender():
    """The real sender, or None when FFL_NOTIFY=0 or the VAPID key is missing (a dev/worktree API never pushes)."""
    if os.environ.get("FFL_NOTIFY", "1") == "0" or not os.environ.get("VAPID_PRIVATE_KEY"):
        return None
    return webpush_sender


def _send_all(pushes: list[dict], send, at: str) -> int:
    subs, sent = db.subscriptions(), 0
    for push in pushes:
        ok, dead = deliver(push, subs, send)
        for ep in dead:
            db.drop_subscription(ep)
        subs = [s for s in subs if s["endpoint"] not in dead]
        if ok:
            db.mark_notices(push["keys"], "sent", at)
            sent += 1
    return sent


def flush(t: datetime | None = None, send=None) -> dict:
    """Send the queue: nothing in quiet hours; switched-off and stale notices are marked, the rest grouped and pushed."""
    send = send or sender()
    if send is None:
        return {"disabled": True}
    t = t or datetime.now().astimezone()
    p, unsent = prefs(), db.unsent_notices()
    if in_quiet(t, p["quiet"]):
        return {"held": len(unsent)}
    pushes, marks = plan(unsent, p, t)
    at = t.isoformat(timespec="seconds")
    for outcome, keys in marks.items():
        db.mark_notices(keys, outcome, at)
    return {"sent": _send_all(pushes, send, at), "pushes": len(pushes), **{k: len(v) for k, v in marks.items()}}


def send_test(send=None) -> dict:
    """Settings' Send test: straight to every device, whatever the hour; not queued."""
    send = send or sender()
    if send is None:
        return {"disabled": True}
    push = {"keys": [], "title": "FFL", "body": "Notifications are on for this device.", "url": "/settings", "tag": "test"}
    subs = db.subscriptions()
    ok, dead = deliver(push, subs, send)
    for ep in dead:
        db.drop_subscription(ep)
    return {"devices": len(subs) - len(dead), "accepted": ok}


# --- I/O: the tick (POST /notify/tick, every 15 min from launchd/com.jonulak.ffl-notify) ---

def check_injuries() -> int:
    """My starters off one fresh mRoster read; queues the ones OUT/Doubtful/IR before their lock."""
    from . import espn_season
    lg = espn_season.fresh_league()
    doc = espn_season.raw_league_get("mRoster")
    flags = espn_season.parse_roster_flags(doc, espn_season.my_team_id(lg))
    week = int(doc.get("scoringPeriodId") or lg.current_week)
    return db.add_notices(injury_notices(flags, week, now()))


def check_waivers(synced: bool = True) -> int:
    """Compare ESPN's last waiver run with the one seen before; on a new run, the claims pending at the last tick are
    won (my WAIVER ADDED rows after the previous run) or lost. Then remember this run and today's pending claims.
    Nothing is touched when the run is unreadable, or when it is new and the log did not sync this tick (its
    WAIVER ADDED rows would be missing and every win would read as a loss): the next tick decides."""
    from . import espn_season, espn_write, season
    run, prev = espn_write.last_waiver_run(), db.get_state("waiver_run")
    if run is None or (run != prev and not synced):
        return 0
    pending = db.get_state("pending_claims", [])
    fresh = espn_write.pending_claims()   # before the notice, and a raise stores nothing: the next tick retries
    lg = espn_season.fresh_league()
    me = espn_season.my_team_id(lg)
    my_adds = {r["player_id"] for r in db.transactions() if r["action"] == "WAIVER ADDED"
               and (t := season.parse_tx_id(r["id"])) and t[1] == me and (prev is None or r["date"] > prev[:16])}
    names = {c["add"]: lg.player_map.get(c["add"]) for c in pending if isinstance(lg.player_map.get(c["add"]), str)}
    n = db.add_notices(waiver_notices(run, prev, pending, my_adds, names, now(), {c["id"] for c in fresh}))
    db.put_state("waiver_run", run)
    db.put_state("pending_claims", fresh)
    return n


def tick() -> dict:
    """Sync the log (moves queue themselves), check injuries and waivers, flush. A step that fails is reported and
    the rest still run: an ESPN outage must not hold back notices already queued."""
    from . import espn_season, journal
    out: dict = {}
    steps = (("moves", lambda: len(journal.sync_transactions(espn_season.fresh_league()))),
             ("injuries", check_injuries),
             ("waivers", lambda: check_waivers(synced=not isinstance(out.get("moves"), dict))),
             ("projections", espn_season.record_projections),
             ("sources", check_sources))
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — reported in the response, which is the launchd log line
            log.warning("notify: %s failed: %s", name, e)
            out[name] = {"error": str(e)}
    try:
        out["flush"] = flush()
    except db.DBError as e:
        out["flush"] = {"error": str(e)}
    return out


def vapid_keys() -> tuple[str, str]:
    """A fresh P-256 pair as Web Push wants them: (public, private), both base64url without padding."""
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64(pub), b64(key.private_numbers().private_value.to_bytes(32, "big"))


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["keys"]:
        pub, priv = vapid_keys()
        print(f"VAPID_PUBLIC_KEY={pub}\nVAPID_PRIVATE_KEY={priv}\nVAPID_SUBJECT=mailto:admin@example.com")
    else:
        print("usage: python -m ffl.notify keys")
