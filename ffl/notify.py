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
from .season import parse_tx_id, slot_label

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


def enqueue_moves(rows: list[dict], *, ref=None) -> int:
    """Queue a league's new log rows. Never fails the sync that found them: a DB error is logged."""
    try:
        return db.add_notices(move_notices(rows, now()), ref=ref)
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

    # A player claimed with several drops (one drop per claim) is several claims: name and count him once.
    targets = list(dict.fromkeys(c["add"] for c in pending))
    won = [pid for pid in targets if pid in my_adds]
    lost = [pid for pid in targets if pid not in my_adds]
    parts = ([f"Won {', '.join(map(name, won))}."] if won else []) + ([f"Lost {', '.join(map(name, lost))}."] if lost else [])
    return [{"key": f"waivers:{run}", "kind": "waivers", "title": f"Waivers: {len(won)} of {len(targets)} players won",
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
    from .sources import on_only  # a source switched off here raises nothing, whatever another host left failing
    return db.add_notices(source_notices(on_only(db.get_state("source_status", {}) or {}), datetime.now().astimezone(), now()))


DEFAULT_PREFS = {"move": True, "injury": True, "waivers": True, "news": True, "watch": True, "quiet": ["23:00", "07:00"]}
MAX_AGE = timedelta(hours=24)
BODY_MAX = 180
_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


SWITCHES = ("move", "injury", "waivers", "news", "watch")


def _home(ref) -> bool:
    return ref is None or ref.home


def prefs(*, ref=None) -> dict:
    """A league's push switches (home when `ref` is None) and the quiet hours, which are every league's. Home's
    switches live beside the quiet hours in the global `prefs` row, as before any other league; another league's in
    its own `notify_prefs` state."""
    shared = {**DEFAULT_PREFS, **db.get_state("prefs", {})}
    if _home(ref):
        return shared
    return {**DEFAULT_PREFS, **(db.get_league_state("notify_prefs", {}, ref=ref) or {}), "quiet": shared["quiet"]}


def _checked(merged: dict) -> dict:
    q = merged["quiet"]
    if not (isinstance(q, list) and len(q) == 2 and all(isinstance(s, str) and _HM.match(s) for s in q)):
        raise ValueError(f"quiet must be two HH:MM times, got {q!r}")
    for k in SWITCHES:
        if not isinstance(merged[k], bool):
            raise ValueError(f"{k} must be true or false, got {merged[k]!r}")
    return merged


def put_prefs(p: dict, *, ref=None) -> dict:
    """Each row read and written in one locked transaction (db.update_*state): two changes at once both land."""
    change = {k: v for k, v in p.items() if k in DEFAULT_PREFS}
    _checked({**prefs(ref=ref), **change})  # refuse before writing anything
    if _home(ref):
        return db.update_state("prefs", lambda cur: _checked({**DEFAULT_PREFS, **(cur or {}), **change}), {})
    quiet = {"quiet": change["quiet"]} if "quiet" in change else {}
    shared = db.update_state("prefs", lambda cur: {**(cur or {}), **quiet}, {})
    mine = db.update_league_state("notify_prefs", lambda cur: {k: {**DEFAULT_PREFS, **(cur or {}), **change}[k]
                                                                for k in SWITCHES}, {}, ref=ref)
    return {**DEFAULT_PREFS, **mine, "quiet": {**DEFAULT_PREFS, **shared}["quiet"]}


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


def _move_of(key: str) -> tuple | str:
    """The move a notice's row belongs to: (timestamp, team), as season.group_transactions groups them, so a claim
    and its drop count once. A key that is not `move:<transaction id>` is a move of its own."""
    p = parse_tx_id(key.removeprefix("move:"))
    return (p[0], p[1]) if p else key


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
        count = len({_move_of(n["key"]) for n in moves})
        pushes.append({"keys": [n["key"] for n in moves], "title": f"{count} league move{'s' if count > 1 else ''}",
                       "body": _join(*_by_team([n["body"] for n in moves])), "url": "/transactions", "tag": "moves"})
    trades = [n for n in live if n["kind"] == "trade"]
    if trades:
        pushes.append({"keys": [n["key"] for n in trades], "title": "Trade",
                       "body": _join([n["body"] for n in trades]), "url": "/transactions", "tag": "trade"})
    for n in live:
        if n["kind"] not in ("move", "trade"):
            pushes.append({"keys": [n["key"]], "title": n["title"], "body": _cap(n["body"]), "url": n["url"], "tag": n["key"]})
    return pushes, marks


# Where a notification opens: Declarative Web Push wants the absolute URL (production; setup.sh sets it).
APP_URL = os.environ.get("FFL_APP_URL", "https://ffl.whitebridgelabs.com")


def payload_for(push: dict, app_url: str = APP_URL) -> dict:
    """The push message. Declarative Web Push (`web_push` 8030, Safari 18.4+): Safari shows the notification and opens
    `navigate` itself, with no service worker in the way; iOS often never fires the worker's notificationclick for a
    Home Screen app in the background (WebKit bug 268797; ffl-e9x.10). The legacy top-level fields stay for a browser
    that ignores `web_push` and hands the message to sw.js's push handler as before."""
    legacy = {k: push[k] for k in ("title", "body", "url", "tag")}  # never `ref`: the league rides in url
    return {"web_push": 8030, "notification": {"title": push["title"], "body": push["body"], "tag": push["tag"],
                                               "navigate": app_url.rstrip("/") + push["url"]}, **legacy}


def deliver(push: dict, subs: list[dict], send) -> tuple[bool, list[str]]:
    """Send to every device. Accepted when any device took it; a 404/410 names a dead subscription."""
    payload = payload_for(push)
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


LABEL_MAX = 22


def league_label(ref) -> str:
    """The league as a push names it: its name, cut at a word to LABEL_MAX with an ellipsis."""
    name = ref.name.strip()
    if len(name) <= LABEL_MAX:
        return name
    cut = name[:LABEL_MAX - 1].rsplit(" ", 1)[0] if " " in name[:LABEL_MAX - 1] else name[:LABEL_MAX - 1]
    return cut.rstrip() + "…"


def league_url(url: str, ref) -> str:
    """A push's page in its league: home's as it always was, another's with ?league= so a tap (or a push queued
    before the open league changed) opens that league, never the last one used."""
    if _home(ref):
        return url
    return f"{url}{'&' if '?' in url else '?'}league={ref.key}"


def for_league(push: dict, ref, named: bool) -> dict:
    """A planned push dressed for its league: the label after the title when more than one league is registered,
    the league's page, and a tag of its own (a device replaces a notification with the same tag)."""
    title = f"{push['title']} · {league_label(ref)}" if named else push["title"]
    tag = push["tag"] if _home(ref) else f"{ref.key}:{push['tag']}"
    return {**push, "title": title, "url": league_url(push["url"], ref), "tag": tag, "ref": ref}


def _send_all(pushes: list[dict], send, at: str) -> int:
    subs, sent = db.subscriptions(), 0
    for push in pushes:
        ok, dead = deliver(push, subs, send)
        for ep in dead:
            db.drop_subscription(ep)
        subs = [s for s in subs if s["endpoint"] not in dead]
        if ok:
            db.mark_notices(push["keys"], "sent", at, ref=push.get("ref"))
            sent += 1
    return sent


def flush(t: datetime | None = None, send=None) -> dict:
    """Send every league's queue: nothing in quiet hours (every league's); each league's switched-off and stale
    notices are marked, the rest grouped within the league and pushed, each naming its league once there are two."""
    from . import leagues
    send = send or sender()
    if send is None:
        return {"disabled": True}
    t = t or datetime.now().astimezone()
    refs = leagues.all_leagues()
    unsent = {r.key: db.unsent_notices(ref=r) for r in refs}
    if in_quiet(t, prefs()["quiet"]):
        return {"held": sum(len(u) for u in unsent.values())}
    at = t.isoformat(timespec="seconds")
    pushes, counts = [], {"off": 0, "expired": 0}
    for r in refs:
        planned, marks = plan(unsent[r.key], prefs(ref=r), t)
        for outcome, keys in marks.items():
            db.mark_notices(keys, outcome, at, ref=r)
            counts[outcome] += len(keys)
        pushes += [for_league(p, r, len(refs) > 1) for p in planned]
    return {"sent": _send_all(pushes, send, at), "pushes": len(pushes), **counts}


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

def check_injuries(*, ref=None) -> int:
    """My starters in the league `ref` names (None: home) off one fresh mRoster read; queues the ones
    OUT/Doubtful/IR before their lock."""
    from . import espn_season
    lg = espn_season.fresh_league(ref)
    doc = espn_season.raw_league_get("mRoster", ref=ref)
    flags = espn_season.parse_roster_flags(doc, espn_season.my_team_id(lg, ref))
    week = int(doc.get("scoringPeriodId") or lg.current_week)
    return db.add_notices(injury_notices(flags, week, now()), ref=ref)


def check_waivers(synced: bool = True, *, ref=None) -> int:
    """Compare ESPN's last waiver run with the one seen before; on a new run, the claims pending at the last tick are
    won (my WAIVER ADDED rows after the previous run) or lost. Then remember this run and today's pending claims.
    Nothing is touched when the run is unreadable, or when it is new and the log did not sync this tick (its
    WAIVER ADDED rows would be missing and every win would read as a loss): the next tick decides. Each league's
    run is its own (its waiver day, its claims)."""
    from . import espn_season, espn_write, season
    run, prev = espn_write.last_waiver_run(ref=ref), db.get_league_state("waiver_run", ref=ref)
    if run is None or (run != prev and not synced):
        return 0
    pending = db.get_league_state("pending_claims", [], ref=ref)
    fresh = espn_write.pending_claims(ref=ref)   # before the notice, and a raise stores nothing: the next tick retries
    lg = espn_season.fresh_league(ref)
    me = espn_season.my_team_id(lg, ref)
    my_adds = {r["player_id"] for r in db.transactions(ref=ref) if r["action"] == "WAIVER ADDED"
               and (t := season.parse_tx_id(r["id"])) and t[1] == me and (prev is None or r["date"] > prev[:16])}
    names = {c["add"]: lg.player_map.get(c["add"]) for c in pending if isinstance(lg.player_map.get(c["add"]), str)}
    n = db.add_notices(waiver_notices(run, prev, pending, my_adds, names, now(), {c["id"] for c in fresh}), ref=ref)
    db.put_league_state("waiver_run", run, ref=ref)
    db.put_league_state("pending_claims", fresh, ref=ref)
    return n


def _steps(out: dict, steps) -> dict:
    """Run each (name, fn) into `out`; a step that fails is reported there and the rest still run."""
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — reported in the response, which is the launchd log line
            log.warning("notify: %s failed: %s", name, e)
            out[name] = {"error": str(e)}
    return out


def league_tick(ref=None) -> dict:
    """One league's share of the tick (None: home): sync its log (moves queue themselves), check its injuries and
    waivers, record its projections."""
    from . import espn_season, journal
    out: dict = {}
    return _steps(out, (("moves", lambda: len(journal.sync_transactions(espn_season.fresh_league(ref), ref=ref))),
                        ("injuries", lambda: check_injuries(ref=ref)),
                        ("waivers", lambda: check_waivers(synced=not isinstance(out.get("moves"), dict), ref=ref)),
                        ("projections", lambda: espn_season.record_projections(ref=ref))))


def tick() -> dict:
    """Every league's share (home's at the top of the answer, the others under `leagues`), then the global work
    once: the sources' health, the flush. A step that fails is reported and the rest still run: an ESPN outage must
    not hold back notices already queued, nor one league's another's."""
    from . import leagues
    out = league_tick()
    others = {r.key: league_tick(r) for r in leagues.all_leagues() if not r.home}
    if others:
        out["leagues"] = others
    _steps(out, (("sources", check_sources),))
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
