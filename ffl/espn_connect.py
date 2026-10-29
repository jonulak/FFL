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

from . import auth, config, db, espn_auth

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
    return STARTS, WRONG_CODES, SENDS, PASTES


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
            raise ConnectError(422, "unknown_login", "ESPN doesn't recognise this login. Sign in to ESPN again.")
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
    """Validate and store the user's cookies; their status. A SWID other than the one stored is a different ESPN
    account and is refused (Jonathan 2026-10-28: disconnect first), before ESPN is asked anything."""
    sw = normalize_swid(swid)
    row = db.credential_row(user_id)
    stored = normalize_swid(row["swid"]) if row and row["swid"] else None   # None too for a row this ring can't open
    if sw and stored and stored.upper() != sw.upper():
        raise ConnectError(409, "different_account", "This is a different ESPN account from the one connected. "
                                                     "Disconnect ESPN in Settings first to switch accounts.")
    s2, sw, proven = validate(espn_s2, swid)
    espn_auth.connect(user_id, s2, sw)
    return {**status(user_id), "proven": proven}


def status(user_id: int) -> dict:
    """The connection, never a value: espn_auth.status, ESPN's last refusal reason, and how many extensions are
    paired."""
    st = espn_auth.status(user_id)
    row = db.credential_row(user_id) if st["failing_since"] else None
    return {**st, "unreadable": bool(st["unreadable"]), "failing_reason": row["failing_reason"] if row else None,
            "devices": len(db.user_devices(user_id))}


def disconnect(user_id: int) -> None:
    """The cookies deleted and every paired extension unpaired with them: one left paired would send the login
    straight back on the next ESPN page load. Reconnecting pairs again."""
    db.revoke_devices(user_id)
    espn_auth.disconnect(user_id)


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
    try:
        return await run_in_threadpool(connect, user_id, espn_s2, swid)
    except ConnectError as e:
        _raise(e)


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


@router.post("/espn/disconnect")
def disconnect_route(request: Request):
    disconnect(auth.current_user(request)["id"])
    return {"ok": True}
