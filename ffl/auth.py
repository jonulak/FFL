"""Sign-in and job-token decisions for the season app: who may sign in, and which requests may run a job.

Pure over the environment and the accounts tables (`db.users`/`identities`/`sessions`); the middleware and the
Google routes call these and decide nothing themselves. Env is read at call time. Sign-up is closed: an identity
signs in only if it already maps to a user or its provider-verified email is in FFL_SIGNUP_EMAILS.
"""
from __future__ import annotations

import functools
import hmac
import json
import logging
import os
from typing import Callable

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException
from joserfc.errors import JoseError
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import db, db_backup

log = logging.getLogger(__name__)

SESSION_COOKIE = "ffl_session"  # the default name; FFL_SESSION_COOKIE overrides it (session_cookie())
OIDC_COOKIE = "ffl_oidc"  # Starlette's signed session: authlib's state and the saved `next`, for the round trip only
OIDC_MAX_AGE = 600
GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"
SESSION_DAYS = 90
JOB_HEADER = "x-ffl-job"

# (method, request path), matched exactly. Everything not named here needs a signed-in session.
PUBLIC: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"), ("HEAD", "/health"), ("GET", "/auth/providers"), ("GET", "/auth/google/start"),
    ("GET", "/auth/google/callback"), ("POST", "/auth/dev")})
# The timers' routes: the job token only (signed in without it is 403, signed out 401).
JOB_ONLY: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/notify/tick"), ("POST", "/news/run"), ("POST", "/transactions/sync"), ("POST", "/refresh")})
# The token or a session: the Roster page's outlook button posts it too.
JOB_OR_USER: frozenset[tuple[str, str]] = frozenset({("POST", "/outlook/refresh")})

SIGNED_OUT = {"error": "signed_out"}
JOB_ONLY_BODY = {"error": "job_only"}


def _listed() -> set[str]:
    return {e.strip().lower() for e in os.environ.get("FFL_SIGNUP_EMAILS", "").split(",") if e.strip()}


def signup_allowed(email: str | None, verified: bool) -> bool:
    """A verified email on FFL_SIGNUP_EMAILS (case-insensitive); `*` as the only entry opens it to any verified email
    (beside emails it is ignored, so a list edit cannot open sign-up by accident)."""
    if not email or not verified:
        return False
    listed = _listed()
    return listed == {"*"} or email.strip().lower() in listed


def sign_in(provider: str, subject: str, email: str | None, verified: bool, name: str | None) -> int | None:
    """The user id for this identity, or None with nothing written. Known identity: its user. Else an allowed
    email: the existing user with that email (linked), or a new one."""
    uid = db.identity_user(provider, subject)
    if uid is not None:
        return uid
    if not signup_allowed(email, verified):
        return None
    return db.sign_in_user(provider, subject, email, name)


def job_ok(header_value: str | None) -> bool:
    """The timers' shared secret; FFL_JOB_TOKEN unset or empty refuses every job."""
    token = os.environ.get("FFL_JOB_TOKEN", "")
    if not token or not header_value:
        return False
    return hmac.compare_digest(header_value.encode(), token.encode())


@functools.lru_cache(maxsize=None)
def _local(url: str) -> bool:
    return db_backup.is_local(url)  # asks the server, so once per URL; tests call `_local.cache_clear()`


def dev_signin_enabled() -> bool:
    """FFL_DEV_SIGNIN=1 and a local (non-RDS) database."""
    if os.environ.get("FFL_DEV_SIGNIN") != "1":
        return False
    url = os.environ.get("FFL_DB_URL", "")
    return bool(url) and _local(url)


def cookie_secure() -> bool:
    """Secure exactly when the app's URL is https (tailnet http test servers must still hold the cookie)."""
    return os.environ.get("FFL_APP_URL", "").startswith("https://")


def decide(method: str, path: str, cookie: str | None, job_header: str | None, *,
           on_slide: Callable[[], None] | None = None) -> tuple[int, dict | None]:
    """(0, the user or None) to pass, else (401 or 403, None). The job token is checked before any session read and
    opens the job routes only; an unknown or expired cookie is signed out like a missing one. A database error
    propagates (a 500), never read as signed out. `on_slide` is called when this read slid the session."""
    key = (method, path)
    if key in PUBLIC:
        return 0, None
    if (key in JOB_ONLY or key in JOB_OR_USER) and job_ok(job_header):
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
        if (scope["method"], scope["path"]) in PUBLIC:  # on the loop: /health never waits behind stalled session reads
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


def oidc_secret() -> str:
    return os.environ.get("FFL_AUTH_SECRET", "")


def _google_env() -> tuple[str, str]:
    return os.environ.get("FFL_GOOGLE_CLIENT_ID", ""), os.environ.get("FFL_GOOGLE_CLIENT_SECRET", "")


def google_enabled() -> bool:
    """Client id, client secret, the OIDC session's secret and FFL_APP_URL (the callback's origin) all set (empty is
    unset)."""
    cid, cs = _google_env()
    return bool(cid and cs and oidc_secret() and os.environ.get("FFL_APP_URL"))


@functools.lru_cache(maxsize=4)
def _google(client_id: str, client_secret: str):
    """authlib's Google client for these credentials: built on first use (the caller reads the env per request), the
    same instance while they hold. Google's metadata is fetched on the first redirect, not here."""
    return OAuth().register("google", client_id=client_id, client_secret=client_secret,
                            server_metadata_url=GOOGLE_METADATA, client_kwargs={"scope": "openid email profile"})


def _google_client():
    if not google_enabled():
        raise HTTPException(503, "google_not_configured")
    return _google(*_google_env())


def _set_session(resp, user_id: int, request: Request):
    token = db.create_session(user_id, request.headers.get("user-agent", ""))
    resp.set_cookie(session_cookie(), token, max_age=SESSION_DAYS * 86400, **_cookie_kwargs())
    return resp


def _signin_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/signin?error={code}", status_code=302)


router = APIRouter()


@router.get("/auth/providers")
def providers():
    return {"google": google_enabled(), "apple": False, "dev": dev_signin_enabled()}


@router.get("/auth/google/start")
async def google_start(request: Request, next: str | None = None):
    google = _google_client()
    app_url = os.environ["FFL_APP_URL"].rstrip("/")  # google_enabled() checked it
    request.session["next"] = safe_next(next)  # rides in ffl_oidc to the callback
    try:
        return await google.authorize_redirect(request, app_url + "/api/auth/google/callback")
    except (OAuthError, httpx.HTTPError, ValueError) as e:  # Google's metadata unreachable
        log.warning("google sign-in start failed: %s", getattr(e, "error", None) or type(e).__name__)
        return _signin_error("failed")


@router.get("/auth/google/callback")
async def google_callback(request: Request):
    google = _google_client()
    next_ = safe_next(request.session.pop("next", None))
    try:
        token = await google.authorize_access_token(request)
    except (OAuthError, JoseError, httpx.HTTPError, ValueError) as e:  # refused, bad state or id_token, no exchange
        log.warning("google sign-in callback failed: %s", getattr(e, "error", None) or type(e).__name__)
        return _signin_error("failed")
    info = (token or {}).get("userinfo") or {}
    if not info.get("sub"):
        return _signin_error("failed")
    uid = await run_in_threadpool(sign_in, "google", str(info["sub"]), info.get("email"),
                                  info.get("email_verified") is True, info.get("name"))
    if uid is None:
        return _signin_error("closed")
    return await run_in_threadpool(_set_session, RedirectResponse(next_, status_code=302), uid, request)


@router.post("/auth/dev")
async def dev_signin(request: Request):
    """Local dev only: sign in as any email (created if absent), FFL_SIGNUP_EMAILS ignored. Off, it is a route that
    does not exist: the body is read only after that check, so it is 404 whatever is posted."""
    if not dev_signin_enabled():
        raise HTTPException(404, "Not Found")
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        body = None
    email = body.get("email") if isinstance(body, dict) else None
    if not isinstance(email, str) or "@" not in email:
        raise HTTPException(422, "email required")

    def signin():
        uid = db.ensure_user(email.strip(), None)
        return _set_session(JSONResponse({"ok": True}), uid, request)
    return await run_in_threadpool(signin)


@router.post("/auth/signout")
def signout(request: Request):
    current_user(request)
    db.delete_session(request.cookies[session_cookie()])  # the middleware passed it, so it is there
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(session_cookie(), **_cookie_kwargs())
    return resp


@router.get("/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"email": user["email"], "name": user["name"]}
