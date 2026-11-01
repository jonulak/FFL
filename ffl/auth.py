"""Sign-in and job-token decisions for the season app: who is signed in, and which requests may run a job.

Sessions live in `db.sessions`; a user signs in with a username and password (`ffl/passwords.py`), the first one through
`POST /setup` while nobody has a password. The middleware calls `decide` and decides nothing itself. Env is read at
call time.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response

from . import appconf, db, espn_connect, passwords

log = logging.getLogger(__name__)

SESSION_COOKIE = "ffl_session"  # the default name; FFL_SESSION_COOKIE overrides it (session_cookie())
SESSION_DAYS = 90
JOB_HEADER = "x-ffl-job"

# (method, request path), matched exactly. Everything not named here needs a signed-in session.
PUBLIC: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"), ("HEAD", "/health"), ("GET", "/setup/status"), ("POST", "/setup"), ("POST", "/auth/signin"),
    # the browser extension's (ffl/espn_connect.py): each checks its own secret (poll secret, device token)
    ("POST", "/espn/pair/start"), ("POST", "/espn/pair/poll"), ("POST", "/espn/cookies")})
# (method, path prefix): an invite's setup link, whose token is its own secret.
PUBLIC_PREFIXES: tuple[tuple[str, str], ...] = (("POST", "/auth/setup/"),)
# The timers' routes: the job token only (signed in without it is 403, signed out 401).
JOB_ONLY: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/notify/tick"), ("POST", "/news/run"), ("POST", "/transactions/sync"), ("POST", "/refresh"),
    ("POST", "/outlook/refresh")})

SIGNED_OUT = {"error": "signed_out"}
JOB_ONLY_BODY = {"error": "job_only"}


def job_ok(header_value: str | None) -> bool:
    """The timers' shared secret; FFL_JOB_TOKEN unset or empty refuses every job."""
    token = os.environ.get("FFL_JOB_TOKEN", "")
    if not token or not header_value:
        return False
    return hmac.compare_digest(header_value.encode(), token.encode())


def cookie_secure() -> bool:
    """Secure exactly when the app's URL is https (tailnet http test servers must still hold the cookie)."""
    return appconf.app_url().startswith("https://")


def public(method: str, path: str) -> bool:
    return (method, path) in PUBLIC or any(method == m and path.startswith(p) for m, p in PUBLIC_PREFIXES)


def decide(method: str, path: str, cookie: str | None, job_header: str | None, *,
           on_slide: Callable[[], None] | None = None) -> tuple[int, dict | None]:
    """(0, the user or None) to pass, else (401 or 403, None). The job token is checked before any session read and
    opens the job routes only; an unknown or expired cookie is signed out like a missing one. A database error
    propagates (a 500), never read as signed out. `on_slide` is called when this read slid the session."""
    key = (method, path)
    if public(method, path):
        return 0, None
    if key in JOB_ONLY and job_ok(job_header):
        return 0, None
    user, slid = db.session_lookup(cookie) if cookie else (None, False)
    if key in JOB_ONLY:
        return (403, None) if user else (401, None)
    if user and slid and on_slide:
        on_slide()
    return (0, user) if user else (401, None)


def session_cookie() -> str:
    """The session cookie's name: FFL_SESSION_COOKIE, else ffl_session. A browser shares cookies across ports, so two
    worktree test servers on one host each name their own (production leaves it unset)."""
    return os.environ.get("FFL_SESSION_COOKIE") or SESSION_COOKIE


def _cookie_kwargs() -> dict:
    """ffl_session's attributes, the same on sign-in, slide and sign-out (Secure read per request)."""
    return {"path": "/", "secure": cookie_secure(), "httponly": True, "samesite": "lax"}


def _session_set_cookie(token: str) -> tuple[bytes, bytes]:
    """The raw Set-Cookie header that (re)sends the session with its full Max-Age."""
    r = Response()
    r.set_cookie(session_cookie(), token, max_age=SESSION_DAYS * 86400, **_cookie_kwargs())
    return next((k, v) for k, v in r.raw_headers if k == b"set-cookie")


class AuthMiddleware:
    """Pure ASGI (BaseHTTPMiddleware would buffer /live's event stream): every http request through `decide`; a
    pass puts the user (None on a public or job request) in scope["state"]["user"]. Other scopes pass untouched.
    A read that slid the session re-sends its cookie on the response's start (the DB's 90 days and the browser's
    Max-Age move together), unless the route set or expired ffl_session itself (sign-in, sign-out)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if public(scope["method"], scope["path"]):  # on the loop: /health never waits behind stalled session reads
            scope.setdefault("state", {})["user"] = None
            return await self.app(scope, receive, send)
        conn = HTTPConnection(scope)
        cookie = conn.cookies.get(session_cookie())
        slid = []
        # The module's decide, looked up per call (tests patch it), in the threadpool: its session read is a sync DB
        # call, and on the loop one slow read would stall every request and /live stream.
        code, user = await run_in_threadpool(decide, scope["method"], scope["path"], cookie,
                                             conn.headers.get(JOB_HEADER), on_slide=lambda: slid.append(True))
        if code:
            body = SIGNED_OUT if code == 401 else JOB_ONLY_BODY
            return await JSONResponse(body, status_code=code, headers={"cache-control": "no-store"})(
                scope, receive, send)
        scope.setdefault("state", {})["user"] = user
        if not slid:
            return await self.app(scope, receive, send)
        prefix = session_cookie().encode() + b"="

        async def send_slid(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"set-cookie" and v.startswith(prefix) for k, v in headers):
                    headers.append(_session_set_cookie(cookie))
                message = {**message, "headers": headers}
            await send(message)
        return await self.app(scope, receive, send_slid)


class SignedOut(HTTPException):
    """`current_user`'s refusal; `signed_out_handler` answers it as the middleware does."""

    def __init__(self):
        super().__init__(401, "signed_out")


async def signed_out_handler(request: Request, exc: SignedOut) -> JSONResponse:
    return JSONResponse(SIGNED_OUT, status_code=401, headers={"cache-control": "no-store"})


def current_user(request: Request) -> dict:
    """The signed-in user the middleware passed, as a route dependency; 401 when there is none (a job request)."""
    user = (request.scope.get("state") or {}).get("user")
    if not user:
        raise SignedOut()
    return user


# --- the sign-in routes ---

def safe_next(raw: str | None) -> str:
    """Where to land after signing in: a path on this site only. `//host` is another origin, and browsers read a
    backslash as a slash and drop tabs and newlines (`/\\host`, `/\\t/host`), so any of those, anywhere, is "/"."""
    if (not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw
            or any(ord(ch) < 0x20 for ch in raw)):
        return "/"
    return raw


def _set_session(resp, user_id: int, request: Request):
    token = db.create_session(user_id, request.headers.get("user-agent", ""))
    resp.set_cookie(session_cookie(), token, max_age=SESSION_DAYS * 86400, **_cookie_kwargs())
    return resp


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


MAX_PASSWORD = 1024  # scrypt hashes whatever it is handed: a cap at the boundary keeps one request cheap
SIGNIN_WINDOW = espn_connect.Window(10, 900)  # wrong passwords per (username, client address) in 15 minutes
_DUMMY_HASH = passwords.hash_password("not-a-password")  # an unknown user costs the same scrypt as a known one


class SignInIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=MAX_PASSWORD)


class PasswordIn(BaseModel):
    current: str = Field(max_length=MAX_PASSWORD)
    new: str = Field(max_length=MAX_PASSWORD)


router = APIRouter()


def _new_login(body: SignInIn) -> tuple[str | None, JSONResponse | None]:
    """(the normalised username, None), or (None, the 422) for a bad username or a short password."""
    username = passwords.check_username(body.username)
    if username is None:
        return None, _error(422, "bad_username")
    if len(body.password) < passwords.MIN_PASSWORD:
        return None, _error(422, "short_password")
    return username, None


@router.get("/setup/status")
def setup_status():
    return {"needs_setup": db.needs_setup()}


@router.post("/setup")
def setup(body: SignInIn, request: Request):
    """The first sign-up, while nobody has a password: the oldest user (made if there is none) becomes this admin.
    After that the route does not exist."""
    if not db.needs_setup():  # before any scrypt: once set up this is a cheap 404
        raise HTTPException(404, "Not Found")
    username, bad = _new_login(body)
    if bad:
        return bad
    try:
        uid = db.claim_first_user(username, passwords.hash_password(body.password))
    except db.Taken:
        return _error(409, "username_taken")
    if uid is None:  # another claim won the race
        raise HTTPException(404, "Not Found")
    origin = request.headers.get("origin") or ""
    if origin.startswith(("http://", "https://")) and not appconf.get(appconf.APP_URL):
        appconf.put(appconf.APP_URL, origin)  # where the first admin opened the app; Settings › App edits it
    return _set_session(JSONResponse({"ok": True}), uid, request)


@router.post("/auth/signin")
def signin(body: SignInIn, request: Request):
    """Username (any case) and password. A wrong password, an unknown user and an invite with no password yet answer
    alike, after the same scrypt; ten wrong tries per name and address in 15 minutes, then 429."""
    username = body.username.strip()
    key = f"{username.lower()}|{request.client.host if request.client else ''}"
    if SIGNIN_WINDOW.full(key):
        return _error(429, "rate_limited")
    user = db.user_by_username(username) if username else None
    stored = user and user["password_hash"]
    if not passwords.verify(body.password, stored or _DUMMY_HASH) or not stored:
        SIGNIN_WINDOW.allow(key)
        return _error(401, "bad_login")
    return _set_session(JSONResponse({"ok": True}), user["id"], request)


@router.post("/auth/password")
def change_password(body: PasswordIn, request: Request):
    """A new password, with the current one; this session and the others stay."""
    user = current_user(request)
    stored = user.get("password_hash")
    if not passwords.verify(body.current, stored or _DUMMY_HASH) or not stored:
        return _error(401, "bad_login")
    if len(body.new) < passwords.MIN_PASSWORD:
        return _error(422, "short_password")
    db.set_password(user["id"], passwords.hash_password(body.new))
    return {"ok": True}


@router.post("/auth/signout")
def signout(request: Request):
    current_user(request)
    db.delete_session(request.cookies[session_cookie()])  # the middleware passed it, so it is there
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(session_cookie(), **_cookie_kwargs())
    return resp


@router.post("/auth/signout-others")
def signout_others(request: Request):
    """Every session of this user's but the one asking."""
    user = current_user(request)
    return {"ended": db.revoke_sessions(user["id"], keep=request.cookies[session_cookie()])}


@router.post("/auth/sessions/{session_id}/revoke")
def revoke(session_id: str, request: Request):
    """End one of this user's sessions (an id off /account); their own current one signs them out here too."""
    user = current_user(request)
    if not db.revoke_session(user["id"], session_id):
        raise HTTPException(404, "unknown_session")
    current = db._token_hash(request.cookies[session_cookie()]) == session_id
    resp = JSONResponse({"ok": True, "signed_out": current})
    if current:
        resp.delete_cookie(session_cookie(), **_cookie_kwargs())
    return resp


class DeleteIn(BaseModel):
    username: str  # the confirmation: the account's own username, typed (any case)


# Called with the user id after an account is deleted (api's: drop their in-memory page caches).
ON_DELETE: list[Callable[[int], None]] = []


def _delete_user(user_id: int) -> None:
    db.delete_user(user_id)
    for fn in ON_DELETE:
        try:
            fn(user_id)
        except Exception:
            log.exception("after deleting user %s", user_id)


@router.post("/auth/delete")
def delete_account(body: DeleteIn, request: Request):
    """The account and every personal row (`db.delete_user`), then signed out."""
    user = current_user(request)
    mine = user.get("username")
    if not mine or body.username.strip().lower() != mine.lower():
        return _error(422, "username_mismatch")
    # the last admin leaving others behind would strand the install: /setup only reopens on an empty users table
    if user.get("is_admin") and db.admin_count() <= 1 and len(db.list_users()) > 1:
        return _error(409, "last_admin")
    _delete_user(user["id"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(session_cookie(), **_cookie_kwargs())
    return resp


@router.get("/account")
def account(request: Request):
    """Settings' Account section: who is signed in and their sessions (this one marked)."""
    user = current_user(request)
    return {**_who(user), "sessions": db.user_sessions(user["id"], current=request.cookies.get(session_cookie()))}


@router.get("/auth/me")
def me(request: Request):
    return _who(current_user(request))


def _who(user: dict) -> dict:
    # .get: conftest's signed-in TEST_USER carries only id, email and name
    return {"username": user.get("username"), "email": user["email"], "name": user["name"],
            "is_admin": bool(user.get("is_admin"))}


# --- admins: invites, set-password links, promotion and deletion ---

def require_admin(request: Request) -> dict:
    """The signed-in user when they are an admin (read from the database, so a demotion holds at once); else 403."""
    user = current_user(request)
    row = db.user_row(user["id"])
    if not (row and row["is_admin"]):
        raise HTTPException(403, {"error": "not_admin", "message": "Only an admin can do that."})
    return user


def _setup_link(user_id: int) -> dict:
    """A fresh setup link for the user (it replaces any earlier one): the app URL's /setup/<token>."""
    return {"id": user_id, "link": f"{appconf.app_url()}/setup/{db.issue_setup_token(user_id)}"}


def _known(user_id: int) -> dict:
    row = db.user_row(user_id)
    if row is None:
        raise HTTPException(404, {"error": "unknown_user"})
    return row


class InviteIn(BaseModel):
    email: str | None = Field(None, max_length=254)
    name: str | None = Field(None, max_length=100)


class AdminFlagIn(BaseModel):
    is_admin: bool


@router.get("/admin/users")
def admin_users(request: Request):
    require_admin(request)
    return {"users": db.list_users()}


@router.post("/admin/users")
def admin_invite(body: InviteIn, request: Request):
    """A new user with no login yet, and the link that sets one (live 7 days)."""
    require_admin(request)
    try:
        uid = db.create_invite((body.email or "").strip() or None, (body.name or "").strip() or None)
    except db.Taken:
        return _error(409, "email_taken")
    return _setup_link(uid)


@router.post("/admin/users/{user_id}/reset")
def admin_reset(user_id: int, request: Request):
    """A new set-password link for an existing user (their current password works until it is used)."""
    require_admin(request)
    _known(user_id)
    return _setup_link(user_id)


@router.post("/admin/users/{user_id}/admin")
def admin_flag(user_id: int, body: AdminFlagIn, request: Request):
    require_admin(request)
    row = _known(user_id)
    # shortcut: count-then-write is not atomic; two admins demoting each other at once could leave none
    if row["is_admin"] and not body.is_admin and db.admin_count() <= 1:
        return _error(409, "last_admin")
    db.set_admin(user_id, body.is_admin)
    return {"ok": True}


@router.post("/admin/users/{user_id}/delete")
def admin_delete(user_id: int, request: Request):
    """Another user's account and personal rows, as /auth/delete does for one's own (which is the way to delete
    oneself, so the caller always remains: never the last admin)."""
    if require_admin(request)["id"] == user_id:
        return _error(409, "self")
    _known(user_id)
    _delete_user(user_id)
    return {"ok": True}


@router.post("/auth/setup/{token}")
def setup_link(token: str, body: SignInIn, request: Request):
    """A setup link (public: the token is its own secret): sets the username and password, ends the link and signs
    them in. A reset link on a user who has a password replaces it."""
    user = db.user_by_setup_token(token)
    if user is None:
        return _error(404, "bad_token")
    username, bad = _new_login(body)
    if bad:
        return bad
    try:
        db.set_username(user["id"], username)
    except db.Taken:
        return _error(409, "username_taken")
    db.set_password(user["id"], passwords.hash_password(body.password))
    db.clear_setup_token(user["id"])
    return _set_session(JSONResponse({"ok": True}), user["id"], request)
