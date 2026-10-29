"""Connecting a user's ESPN login (ffl-soc.6.3.3): the browser extension's pairing, the cookies it sends, the manual
paste, and the connection's status. The cookies themselves are ffl/espn_auth.py's (sealed per user, ffl-soc.3).

Pairing is a device-code flow started IN THE EXTENSION: it asks for a code (POST /espn/pair/start, no session) and
shows it; the signed-in user types it into the app (POST /espn/pair/confirm); the extension's poll (POST
/espn/pair/poll) then receives a device token whose only use is POST /espn/cookies. The direction is a security
property, never flip it: a phished user can at worst land an attacker's ESPN login in their own account, never hand
their login to the attacker's.

Never logged or echoed: a cookie value, a code, a secret or token, or the fan API's URL (it carries the SWID). The
fan API is called with urllib, which logs nothing (httpx logs every URL at INFO). Request bodies are parsed by hand:
FastAPI's 422 would echo the posted cookies back.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, espn_auth, leagues

log = logging.getLogger(__name__)

PAIR_TTL_S = 600
POLL_EVERY_S = 2
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: read off one screen, typed on another
CODE_LEN = 8
OPEN_PAIRINGS_MAX = 300   # unconfirmed live pairings, everyone's: the ceiling on unauthenticated writes
CHECK_LEAGUES = 3         # private leagues tried, at most, to prove espn_s2
FAN_URL = ("https://fan.api.espn.com/apis/v2/fans/{swid}?displayHiddenPrefs=true&context=fantasy&useCookieAuth=true"
           "&source=fantasyapp-ios&featureFlags=challengeEntries")
FFL_GAME = 1              # the fan API's gameId for fantasy football
EXTENSION_ORIGINS = r"^(chrome-extension|safari-web-extension)://[A-Za-z0-9-]+$"   # api's CORS: the extension only
TIMEOUT_S = 15

_GUID = re.compile(r"^\{?([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}?$")
_S2 = re.compile(r"^[A-Za-z0-9%+/=_.\-]{100,4000}$")


class ConnectError(Exception):
    """A connect refused, with a plain reason; nothing was stored."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Window:
    """At most `limit` hits per key in any `per_s` seconds, in this process (the API runs one worker)."""

    def __init__(self, limit: int, per_s: float):
        self.limit, self.per_s = limit, per_s
        self._hits: dict[object, deque] = {}
        self._lock = threading.Lock()

    def _live(self, key, now: float) -> deque:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.per_s:
            q.popleft()
        return q

    def full(self, key) -> bool:
        with self._lock:
            return len(self._live(key, time.monotonic())) >= self.limit

    def allow(self, key) -> bool:
        """Counts a hit unless the window is full; False when it is."""
        now = time.monotonic()
        with self._lock:
            q = self._live(key, now)
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # forget idle keys
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.per_s]:
                    del self._hits[k]
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


STARTS = Window(10, 600)          # per client address
WRONG_CODES = Window(5, 600)      # per signed-in user
SENDS = Window(20, 3600)          # per paired extension
PASTES = Window(10, 3600)         # per signed-in user


def _limits() -> tuple[Window, ...]:
    return STARTS, WRONG_CODES, SENDS, PASTES, LISTS, PICKS


# --- the cookies ---

def normalize_swid(raw: str) -> str | None:
    """A signed-in SWID as the cookie holds it, {GUID} (percent-decoded, braces put back); None for anything else (an
    anonymous visitor's SWID has no braces, but neither does a paste that dropped them: the fan API tells them apart)."""
    m = _GUID.match(urllib.parse.unquote(raw.strip()).strip())
    return "{" + m.group(1) + "}" if m else None


def check_s2(raw: str) -> str | None:
    """espn_s2 exactly as the cookie holds it (URL-encoded: never decoded here), or None when it cannot be one."""
    s2 = raw.strip()
    return s2 if _S2.match(s2) else None


@dataclass(frozen=True)
class Entry:
    league_id: str
    team_id: int
    season: int
    league_name: str
    size: int | None


def fan_entries(swid: str) -> list[Entry]:
    """The SWID's fantasy football entries this season off ESPN's fan API (no cookies needed). ConnectError 422 when
    ESPN does not know the SWID, 502 when ESPN cannot be read."""
    url = FAN_URL.format(swid=urllib.parse.quote(swid, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            doc = json.load(r)
    except urllib.error.HTTPError as e:
        if 400 <= e.code < 500:
            raise ConnectError(422, "unknown_login", "ESPN doesn't know this browser's login: it's out of date. Sign "
                                                     "out of ESPN in this browser, then sign in again.")
        raise ConnectError(502, "espn_unavailable", "ESPN didn't answer. Try again in a minute.")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise ConnectError(502, "espn_unavailable", "ESPN didn't answer. Try again in a minute.")
    return parse_entries(doc, config.SEASON)


def parse_entries(doc: dict, season: int) -> list[Entry]:
    out = []
    for p in doc.get("preferences") or []:
        e = ((p or {}).get("metaData") or {}).get("entry") or {}
        g = (e.get("groups") or [{}])[0] or {}
        if e.get("gameId") != FFL_GAME or e.get("seasonId") != season or not g.get("groupId"):
            continue
        try:
            out.append(Entry(str(g["groupId"]), int(e["entryId"]), season, str(g.get("groupName") or ""),
                             g.get("groupSize")))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _league_status(entry: Entry, jar: dict | None) -> int:
    """The HTTP status of one league read (mSettings) under `jar`, or under no cookies; 0 when ESPN cannot be reached.
    Read directly rather than through espn_api, which turns a 403 into a bare ESPNUnknownError."""
    import requests
    from espn_api.requests.constant import FANTASY_BASE_ENDPOINT
    url = f"{FANTASY_BASE_ENDPOINT}ffl/seasons/{entry.season}/segments/0/leagues/{int(entry.league_id)}"
    try:
        return requests.get(url, params={"view": "mSettings"}, cookies=jar, timeout=TIMEOUT_S).status_code
    except requests.RequestException:
        return 0


def validate(espn_s2: str, swid: str) -> tuple[str, str, bool]:
    """(espn_s2, SWID) ready to store, and whether ESPN proved espn_s2: a private league of theirs read with it. A
    public league reads with any cookies, so when the first CHECK_LEAGUES are all public espn_s2 is stored unproven
    and a later refusal marks it failing (soc.3). ConnectError when ESPN refuses them or lists no league."""
    s2, sw = check_s2(espn_s2), normalize_swid(swid)
    if sw is None:
        raise ConnectError(422, "bad_swid", "That SWID isn't a signed-in ESPN login. Sign in to ESPN and try again.")
    if s2 is None:
        raise ConnectError(422, "bad_espn_s2", "That espn_s2 doesn't look right. Copy the whole value again.")
    entries = fan_entries(sw)
    if not entries:
        raise ConnectError(422, "no_leagues", f"ESPN lists no fantasy football league for this login in "
                                              f"{config.SEASON}. Is it the ESPN account your league is on?")
    jar = {"espn_s2": s2, "SWID": sw}
    unavailable = ConnectError(502, "espn_unavailable", "ESPN didn't answer. Try again in a minute.")
    for e in entries[:CHECK_LEAGUES]:
        bare = _league_status(e, None)
        if bare == 200:
            continue                     # public: proves nothing
        if bare not in (401, 403):
            log.warning("ESPN league read answered %s while connecting", bare)
            raise unavailable
        mine = _league_status(e, jar)
        if mine == 200:
            return s2, sw, True
        if mine in (401, 403):
            raise ConnectError(422, "refused", "ESPN refused this login. Sign in to ESPN again, then reconnect.")
        log.warning("ESPN league read answered %s while connecting", mine)
        raise unavailable
    return s2, sw, False


def connect(user_id: int, espn_s2: str, swid: str) -> dict:
    """Validate and store the user's cookies; their status, with `account` the id of the account they landed on. A
    SWID the user has not connected before is another ESPN account, kept beside the others (#460, Jonathan
    2026-10-28): a user may follow leagues from several."""
    s2, sw, proven = validate(espn_s2, swid)
    account = espn_auth.connect(user_id, s2, sw)
    row = next(r for r in db.credential_rows(user_id) if r["swid_hash"] == account)
    return {**status(user_id), "proven": proven, "account": row["id"]}


MAX_OTHERS = 4                 # SWIDs an extension sends beside the first (a browser holding several: content.ts)
UNKNOWN = ("bad_swid", "unknown_login")


def connect_any(user_id: int, espn_s2: str, swid: str, others: list[str]) -> dict:
    """connect() with the first of `swid`, then `others`, that ESPN knows: ESPN's scripts write SWID too, so a browser
    can hold a stale one beside the login's, and the extension can't tell them apart. A refusal past the SWID (refused,
    no_leagues, ESPN down) is the answer, never a reason to try the next."""
    tries = [swid, *others[:MAX_OTHERS]]
    for i, sw in enumerate(tries):
        try:
            return connect(user_id, espn_s2, sw)
        except ConnectError as e:
            if e.code not in UNKNOWN or i == len(tries) - 1:
                raise
    raise AssertionError("unreachable")


# A send the extension made that ESPN refused, per user (code + message, never a value): /espn/status shows it so the
# wizard waiting for the login can say why none arrived. A connect clears it; it lapses after REFUSAL_TTL_S.
REFUSAL_TTL_S = 15 * 60
_REFUSALS: dict[int, dict] = {}


def _refused(user_id: int) -> dict | None:
    r = _REFUSALS.get(user_id)
    return r if r and time.time() - r["at"] < REFUSAL_TTL_S else None


def status(user_id: int) -> dict:
    """The connection, never a value: espn_auth.status (each account with its id), each account's leagues here
    (`leagues`: the keys of the memberships it serves; a membership naming none is the first account's), ESPN's
    earliest refusal reason, and how many extensions are paired."""
    rows = db.credential_rows(user_id)
    st = espn_auth.status(user_id, rows)   # one read: the accounts and their leagues line up
    first = rows[0]["swid_hash"] if rows else None
    serves: dict[str, list[str]] = {}
    for ref in leagues.for_user(user_id):
        serves.setdefault(ref.espn_account or first, []).append(ref.key)
    accts = [{**a, "unreadable": bool(a["unreadable"]), "leagues": serves.get(r["swid_hash"], [])}
             for a, r in zip(st["accounts"], rows)]
    failing = sorted((a for a in accts if a["failing_since"]), key=lambda a: a["failing_since"])
    return {**st, "accounts": accts, "unreadable": bool(st["unreadable"]),
            "failing_reason": failing[0]["failing_reason"] if failing else None,
            "devices": len(db.user_devices(user_id)), "refused": _refused(user_id)}


ON_LEAVE: list = []   # callbacks (user_id, league keys) when a disconnect takes the user out of leagues (api's caches)


def disconnect(user_id: int, account_id: int | None = None) -> list[str]:
    """One ESPN account (`account_id`), or with None every one, and with it the user's place in every league it
    served (#460, Jonathan 2026-10-28: a login removed must take its leagues' access with it, or the league would read
    on through a league-mate's login as if another member used the app). With None every paired extension is unpaired
    too: one left paired would send a login straight back on the next ESPN page load. The league keys left.
    ConnectError 404 for an account that is not theirs."""
    rows = db.credential_rows(user_id)
    first = rows[0]["swid_hash"] if rows else None
    refs = leagues.for_user(user_id)
    if account_id is None:
        left = [r.key for r in refs]
    else:
        row = next((r for r in rows if r["id"] == account_id), None)
        if row is None or row["swid_hash"] is None:
            raise ConnectError(404, "unknown_account", "No such ESPN account.")
        left = [r.key for r in refs if (r.espn_account or first) == row["swid_hash"]]
    db.drop_memberships(user_id, left)
    if account_id is None:
        db.revoke_devices(user_id)
        espn_auth.disconnect(user_id)
    else:
        espn_auth.disconnect(user_id, row["swid_hash"])
    with _CHOICES_LOCK:
        _CHOICES.pop(user_id, None)
    for fn in ON_LEAVE:
        fn(user_id, left)
    return left


# --- the user's leagues (ffl-soc.6.3.4) ---
# Listed off the fan API under the SWIDs in the user's OWN stored cookies, never one a client names: a SWID alone
# lists someone's leagues. A user adds or joins only a league their own entries list, never an id they type, and a
# league they are not in answers exactly as one that does not exist. Every account the user has connected is listed
# (#460), or one (`account`: the wizard adds one login at a time); a league under two of them is the first-connected
# one's unless only the other can pick it. A league that cannot be picked is still listed, with its `problem`.

CHOICES_TTL_S = 300
MAX_PICKS = 20
# user -> (when, the accounts it was read for, {league_id: choice}, problems)
_CHOICES: dict[int, tuple[float, tuple, dict[str, dict], list[dict]]] = {}
_CHOICES_LOCK = threading.Lock()
_CONNECTING: dict[int, threading.Lock] = {}                    # one add-or-join at a time per user
LISTS = Window(30, 600)           # per signed-in user: ESPN reads behind the picker
PICKS = Window(60, 3600)          # per signed-in user: add-or-join posts (the wizard posts one league at a time)
PICK_MAX_AGE_S = 60               # an add-or-join re-reads ESPN's list only when the user's is older than this

# why a listed league cannot be picked: title, the short line (a hover), and how to fix it (the dialog)
PROBLEMS = {
    "refused": ("ESPN refused this login",
                "ESPN refused this login, so FFL can't read this league.",
                "Open ESPN in the browser where the FFL extension is on and sign in with this login again. The "
                "extension sends the new login, and the league can be picked once it arrives."),
    "not_your_team": ("Not your team",
                      "ESPN doesn't show you as this team's manager.",
                      "FFL follows a team for its manager or co-manager only. If this is your team, sign in to ESPN "
                      "with the login that manages it."),
    "espn_unavailable": ("ESPN didn't answer",
                         "ESPN didn't answer for this league.",
                         "This is usually brief. Check again in a minute."),
}


def problem(code: str) -> dict:
    title, message, fix = PROBLEMS[code]
    return {"error": code, "title": title, "message": message, "fix": fix}


def _league_doc(entry: Entry, mine: espn_auth.Cookies) -> dict:
    """The league's settings and teams (mSettings + mTeam) under the user's own cookies, never a league-mate's: they
    are no member yet. ESPN refusing them marks them failing (soc.3: the Reconnect banner reads it)."""
    import requests
    from espn_api.requests.constant import FANTASY_BASE_ENDPOINT
    url = f"{FANTASY_BASE_ENDPOINT}ffl/seasons/{entry.season}/segments/0/leagues/{int(entry.league_id)}"
    unavailable = ConnectError(502, "espn_unavailable", "ESPN didn't answer. Try again in a minute.")
    try:
        r = requests.get(url, params=[("view", "mSettings"), ("view", "mTeam")], cookies=mine.jar(),
                         timeout=TIMEOUT_S)
    except requests.RequestException:
        raise unavailable
    if r.status_code in (401, 403):
        espn_auth.refused(mine, f"ESPN refused a league read while listing leagues ({r.status_code})")
        raise ConnectError(422, "refused", "ESPN refused this login. Sign in to ESPN again, then reconnect.")
    if r.status_code != 200:
        log.warning("ESPN league read answered %s while listing leagues", r.status_code)
        raise unavailable
    try:
        doc = r.json()
    except ValueError:
        raise unavailable
    return (doc[0] if isinstance(doc, list) and doc else doc) or {}


def team_name(team: dict) -> str:
    return team.get("name") or f"{team.get('location', '')} {team.get('nickname', '')}".strip()


def choice(entry: Entry, row: dict, doc: dict | None, failed: str | None = None) -> dict:
    """One league as the picker shows it: ESPN's name and size, the user's team (the fan entry's) with its name and
    logo when the league could be read (`doc`), the account it came through (`account`, its id), and `problem`
    (None, or why it cannot be picked: `failed`, the read's error, else a team whose owners lack the account's SWID).
    `_account` (the swid_hash) stays on the server."""
    s = (doc or {}).get("settings") or {}
    team = next((t for t in (doc or {}).get("teams") or [] if t.get("id") == entry.team_id), None)
    if failed is None and doc is not None:
        owners = {str(o).upper() for o in (team or {}).get("owners") or []}
        failed = None if team is not None and row["swid"].upper() in owners else "not_your_team"
    return {"league_id": entry.league_id, "key": f"espn:{entry.league_id}", "season": entry.season,
            "name": s.get("name") or entry.league_name, "size": s.get("size") or entry.size,
            "team_id": entry.team_id, "team": team_name(team) if team else None,
            "logo": (team or {}).get("logo") or None, "account": row["id"], "_account": row["swid_hash"],
            "ok": failed is None, "problem": problem(failed) if failed else None}


def _account_choices(row: dict) -> list[dict]:
    """One account's leagues off the fan API (its SWID alone: an account ESPN has refused still lists them), each read
    under its cookies unless they are refused already; ConnectError when the fan API cannot list them."""
    entries = fan_entries(row["swid"])
    if row["failing_since"]:
        return [choice(e, row, None, "refused") for e in entries]
    mine = espn_auth.Cookies(row["user_id"], row["espn_s2"], row["swid"], row["id"], row["swid_hash"])

    def read(e: Entry) -> tuple[dict | None, str | None]:
        try:
            return _league_doc(e, mine), None
        except ConnectError as err:
            return None, err.code

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(entries)))) as pool:
        reads = list(pool.map(read, entries))
    refused = any(code == "refused" for _, code in reads)   # one refusal is the login's: none of its leagues read
    return [choice(e, row, None if refused else doc, "refused" if refused else code)
            for e, (doc, code) in zip(entries, reads)]


def _choices(user_id: int, fresh: bool = False, max_age: float = CHOICES_TTL_S) -> tuple[dict[str, dict], list[dict]]:
    """({league_id: choice}, problems) over every account of the user's this ring can open this season, kept
    CHOICES_TTL_S per user (and per set of accounts and their state: a new, changed or refused login is a new list).
    An account the fan API cannot list is a problem ({account, error, message}) and the others still list; every one
    failing raises the first's. ConnectError 409 with no account."""
    rows = [r for r in db.credential_rows(user_id) if not r["unreadable"]]
    if not rows:
        raise ConnectError(409, "not_connected", "Connect ESPN first.")
    seen = tuple((r["id"], r["swid"], r["failing_since"]) for r in rows)
    now = time.monotonic()
    with _CHOICES_LOCK:
        hit = _CHOICES.get(user_id)
    if not fresh and hit and hit[1] == seen and now - hit[0] < max_age:
        return hit[2], hit[3]
    out: dict[str, dict] = {}
    problems: list[dict] = []
    errors: list[ConnectError] = []
    for row in rows:
        try:
            for c in _account_choices(row):
                had = out.get(c["league_id"])
                if had is None or (not had["ok"] and c["ok"]):   # the first-connected account's, unless only a later
                    out[c["league_id"]] = c                       # one can pick it
        except ConnectError as e:
            errors.append(e)
            problems.append({"account": row["id"], "error": e.code, "message": e.message})
    if len(errors) == len(rows):
        raise errors[0]
    seen = tuple((r["id"], r["swid"], r["failing_since"]) for r in db.credential_rows(user_id) if not r["unreadable"])
    with _CHOICES_LOCK:
        _CHOICES[user_id] = (now, seen, out, problems)   # keyed on the state after the reads (a refusal marks one)
    return out, problems


def league_choices(user_id: int, account_id: int | None = None, fresh: bool = False) -> dict:
    """GET /espn/leagues: the user's ESPN fantasy football leagues this season over every account they have
    connected (Settings: a login that has since been refused shows its leagues greyed, each with its problem), or
    account `account_id` alone (the wizard, the login just connected: refused there, it is the wizard's error, 422
    `refused`, and nothing is listed to add), each marked `joined` when they are a member here already (read now,
    never cached), and the accounts that could not be listed (`problems`). ConnectError 404 for an account that is
    not theirs."""
    row = next((r for r in db.credential_rows(user_id) if r["id"] == account_id), None)
    if account_id is not None and row is None:
        raise ConnectError(404, "unknown_account", "No such ESPN account.")
    joined = {r.key for r in leagues.for_user(user_id)}
    out, problems = _choices(user_id, fresh=fresh)   # fresh: Settings' Check again
    if row is not None and any(r["id"] == account_id and r["failing_since"] for r in db.credential_rows(user_id)):
        raise ConnectError(422, "refused", "ESPN refused this login. Sign in to ESPN again, then reconnect.")
    failed = next((p for p in problems if p["account"] == account_id), None) if row is not None else None
    if failed is not None:   # the fan API couldn't list the wizard's login: its error, not an empty list
        raise ConnectError(502 if failed["error"] == "espn_unavailable" else 422, failed["error"], failed["message"])
    rows = sorted((c for c in out.values() if account_id in (None, c["account"])), key=lambda c: c["name"].lower())
    return {"season": config.SEASON, "problems": [p for p in problems if account_id in (None, p["account"])],
            "leagues": [{**{k: v for k, v in c.items() if k != "_account"}, "joined": c["key"] in joined}
                        for c in rows]}


def leave(user_id: int, key: str) -> None:
    """The user stops following league `key` (Settings' switch): their membership and its pages go, the league and
    their own records there stay (db.drop_memberships). ConnectError 404 when they are not in it."""
    if not any(r.key == key for r in leagues.for_user(user_id)):
        raise ConnectError(404, "unknown_league", "You're not following that league.")
    db.drop_memberships(user_id, [key])
    for fn in ON_LEAVE:
        fn(user_id, [key])


def connect_leagues(user_id: int, ids: list[str], default: str | None) -> dict:
    """POST /espn/leagues: add or join each picked league (leagues.connect) through the account that lists it, one
    plain answer per league; one failing stops none of the others. `default` (one of `ids`) becomes the user's
    default league; none named, the first pick does only when they had no league."""
    picks = list(dict.fromkeys(str(i).strip() for i in ids))
    if not picks or len(picks) > MAX_PICKS or (default is not None and default not in picks):
        raise ConnectError(400, "bad_picks", f"Pick 1 to {MAX_PICKS} of your leagues, the default among them.")
    lock = _CONNECTING.setdefault(user_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ConnectError(409, "busy", "Your leagues are being set up already.")
    try:
        # what ESPN listed within the last minute: the picker's own read, not one more full read per league posted
        choices, _ = _choices(user_id, max_age=PICK_MAX_AGE_S)
        if default is None and leagues.default_for(user_id) is None:
            default = picks[0]
        results = []
        for lid in picks:
            c = choices.get(lid)
            if c is None:
                results.append({"league_id": lid, "ok": False, "error": "unknown_league",
                                "message": "ESPN doesn't list you in that league this season."})
                continue
            out = {"league_id": lid, "key": c["key"], "name": c["name"]}
            if c["problem"]:
                results.append({**out, "ok": False, "error": c["problem"]["error"], "message": c["problem"]["message"]})
                continue
            try:
                leagues.connect(user_id, lid, c["season"], c["team_id"], account=c["_account"], name=c["name"],
                                default=lid == default)
            except Exception as e:   # one league's ESPN read or store; the rest go on
                log.warning("setting up league %s failed: %s: %s", c["key"], type(e).__name__, str(e)[:200])
                results.append({**out, "ok": False, "error": "setup_failed",
                                "message": "This league couldn't be set up. Try it again in a minute."})
                continue
            results.append({**out, "ok": True})
        mine = leagues.default_for(user_id)
        return {"leagues": results, "default": mine.key if mine else None}
    finally:
        lock.release()


# --- pairing ---

def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))


def normalize_code(raw: str) -> str:
    """What the user typed, as the code was made: upper case, dashes and spaces dropped."""
    return re.sub(r"[^A-Z0-9]", "", raw.upper())


def shown(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def device_label(ua: str) -> str:
    """"Safari on iPhone", "Chrome on Mac" ... off the extension's user agent: how Settings names a paired extension."""
    ua = ua or ""
    device = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
              else "Windows" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua
              else None)
    browser = ("Edge" if "Edg/" in ua else "Chrome" if ("Chrome/" in ua or "CriOS" in ua) else "Firefox"
               if "Firefox/" in ua else "Safari" if "Safari/" in ua else None)
    if browser and device:
        return f"{browser} on {device}"
    return browser or device or "Browser extension"


def start_pairing(ua: str) -> dict:
    """A new pairing: the code to show and the secret to poll with. ConnectError 429 at the global ceiling."""
    if db.open_pairings() >= OPEN_PAIRINGS_MAX:
        raise ConnectError(429, "busy", "Too many connections are waiting. Try again in a few minutes.")
    for attempt in range(3):   # a code is unique among stored pairings: a clash (31^8 codes) draws again
        code, poll_secret = new_code(), secrets.token_urlsafe(32)
        try:
            db.create_pairing(code, poll_secret, device_label(ua), PAIR_TTL_S)
            break
        except db.IntegrityError:
            if attempt == 2:
                raise
    return {"code": shown(code), "poll_secret": poll_secret, "expires_in": PAIR_TTL_S, "interval": POLL_EVERY_S}


# --- routes ---

router = APIRouter()


def _client(request: Request) -> str:
    """The caller's address: the first X-Forwarded-For entry (Caddy writes the real client there, Next appends),
    else the socket's. Only a rate-limit key: the global ceiling is what holds."""
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "")


async def _body(request: Request, *names: str) -> list[str]:
    """The named string fields of a JSON body, parsed by hand so no error ever echoes what was posted."""
    try:
        doc = await request.json()
    except ValueError:
        doc = None
    vals = [doc.get(n) if isinstance(doc, dict) else None for n in names]
    if not all(isinstance(v, str) and v for v in vals):
        raise HTTPException(400, {"error": "bad_body", "message": f"Expected {', '.join(names)}."})
    return vals


def _raise(e: ConnectError):
    raise HTTPException(e.status, {"error": e.code, "message": e.message})


@router.post("/espn/pair/start")
async def pair_start(request: Request):
    """No session (the extension's browser has none): a code to show and a secret to poll with, 10 minutes."""
    if not STARTS.allow(_client(request)):
        raise HTTPException(429, {"error": "slow_down", "message": "Too many tries. Wait a few minutes."})
    try:
        return await run_in_threadpool(start_pairing, request.headers.get("user-agent", ""))
    except ConnectError as e:
        _raise(e)


@router.post("/espn/pair/poll")
async def pair_poll(request: Request):
    """No session; the poll secret is the credential. pending, then connected once with the device token."""
    (poll_secret,) = await _body(request, "poll_secret")
    token = "ffld_" + secrets.token_urlsafe(32)
    state, device_id = await run_in_threadpool(db.poll_pairing, poll_secret, token)
    if state == "expired":
        raise HTTPException(410, {"error": "expired", "message": "That code has expired. Start again."})
    if state == "pending":
        return {"status": "pending"}
    return {"status": "connected", "device_token": token, "device_id": device_id}


@router.post("/espn/pair/confirm")
async def pair_confirm(request: Request):
    """The signed-in user types the code the extension shows; five wrong codes in ten minutes and they wait."""
    user = auth.current_user(request)
    (raw,) = await _body(request, "code")
    if WRONG_CODES.full(user["id"]):   # checked before the code is tried, so the limit stops guessing
        raise HTTPException(429, {"error": "slow_down", "message": "Too many wrong codes. Wait ten minutes."})
    code = normalize_code(raw)
    label = await run_in_threadpool(db.confirm_pairing, code, user["id"]) if len(code) == CODE_LEN else None
    if label is None:
        WRONG_CODES.allow(user["id"])
        raise HTTPException(404, {"error": "unknown_code",
                                  "message": "That code isn't waiting. Check it, or tap Connect in the extension again."})
    return {"ok": True, "label": label}


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h[:7].lower() == "bearer " else ""


@router.post("/espn/cookies")
async def cookies(request: Request):
    """A paired extension sends the user's ESPN cookies (device token as the bearer): validated, then stored."""
    token = _bearer(request)
    found = await run_in_threadpool(db.device_user, token) if token else None
    if found is None:
        raise HTTPException(401, {"error": "unknown_device", "message": "This extension isn't paired. Connect again."})
    device_id, user_id = found
    if not SENDS.allow(device_id):
        raise HTTPException(429, {"error": "slow_down", "message": "Too many sends. Wait an hour."})
    espn_s2, swid = await _body(request, "espn_s2", "swid")
    others = await _others(request)
    try:
        out = await run_in_threadpool(connect_any, user_id, espn_s2, swid, others)
    except ConnectError as e:
        log.warning("extension send refused: %s", e.code)  # the reason's code only: never a value
        if e.status == 422:
            _REFUSALS[user_id] = {"error": e.code, "message": e.message, "at": time.time()}
        _raise(e)
    _REFUSALS.pop(user_id, None)
    return {**out, "refused": None}


async def _others(request: Request) -> list[str]:
    """The extension's optional `others`: the browser's other SWIDs (strings), else none."""
    try:
        doc = await request.json()
    except ValueError:
        return []
    vals = doc.get("others") if isinstance(doc, dict) else None
    return [v for v in vals if isinstance(v, str) and v][:MAX_OTHERS] if isinstance(vals, list) else []


@router.post("/espn/connect")
async def connect_by_hand(request: Request):
    """The manual paste (desktop, no extension): the same validation as the extension's send."""
    user = auth.current_user(request)
    if not PASTES.allow(user["id"]):
        raise HTTPException(429, {"error": "slow_down", "message": "Too many tries. Wait an hour."})
    espn_s2, swid = await _body(request, "espn_s2", "swid")
    try:
        return await run_in_threadpool(connect, user["id"], espn_s2, swid)
    except ConnectError as e:
        log.warning("pasted login refused: %s", e.code)
        _raise(e)


@router.get("/espn/status")
def get_status(request: Request):
    return status(auth.current_user(request)["id"])


@router.get("/espn/devices")
def devices(request: Request):
    return {"devices": db.user_devices(auth.current_user(request)["id"])}


@router.post("/espn/devices/{device_id}/revoke")
def revoke(device_id: int, request: Request):
    if not db.revoke_device(auth.current_user(request)["id"], device_id):
        raise HTTPException(404, {"error": "unknown_device", "message": "No such extension."})
    return {"ok": True}


@router.post("/espn/leave")
async def leave_route(request: Request):
    """Stop following one league: {league: key}."""
    user = auth.current_user(request)
    (key,) = await _body(request, "league")
    try:
        await run_in_threadpool(leave, user["id"], key)
    except ConnectError as e:
        _raise(e)
    return {"ok": True}


@router.post("/espn/disconnect")
async def disconnect_route(request: Request):
    """Every ESPN account, or with {account: id} that one alone, and the user's place in the leagues it served:
    {ok, left: [league keys]}."""
    user = auth.current_user(request)
    try:
        doc = await request.json()
    except ValueError:
        doc = None
    account = doc.get("account") if isinstance(doc, dict) else None
    if account is not None and (not isinstance(account, int) or isinstance(account, bool)):
        raise HTTPException(400, {"error": "bad_body", "message": "Expected account: an id, or nothing."})
    try:
        left = await run_in_threadpool(disconnect, user["id"], account)
    except ConnectError as e:
        _raise(e)
    return {"ok": True, "left": left}


@router.get("/espn/leagues")
def get_leagues(request: Request, account: int | None = None, fresh: bool = False):
    """The leagues the user's own ESPN logins are in this season, to pick from; `account`: that login's alone."""
    user = auth.current_user(request)
    if not LISTS.allow(user["id"]):
        raise HTTPException(429, {"error": "slow_down", "message": "Too many tries. Wait a few minutes."})
    try:
        return league_choices(user["id"], account, fresh)
    except ConnectError as e:
        _raise(e)


@router.post("/espn/leagues")
async def post_leagues(request: Request):
    """Add or join the picked leagues: {ids: [ESPN league id, ...], default: one of them or null}."""
    user = auth.current_user(request)
    try:
        doc = await request.json()
    except ValueError:
        doc = None
    ids = doc.get("ids") if isinstance(doc, dict) else None
    default = doc.get("default") if isinstance(doc, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, (str, int)) and not isinstance(i, bool) for i in ids) \
            or not (default is None or isinstance(default, (str, int))):
        raise HTTPException(400, {"error": "bad_body", "message": "Expected ids, and default or null."})
    if not PICKS.allow(user["id"]):
        raise HTTPException(429, {"error": "slow_down", "message": "Too many tries. Wait an hour."})
    try:
        return await run_in_threadpool(connect_leagues, user["id"], [str(i) for i in ids],
                                       None if default is None else str(default))
    except ConnectError as e:
        _raise(e)
