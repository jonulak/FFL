"""Sign-in and job-token decisions for the season app: who may sign in, and which requests may run a job.

Pure over the environment and the accounts tables (`db.users`/`identities`/`sessions`); the middleware and the
Google routes call these and decide nothing themselves. Env is read at call time. Sign-up is closed: an identity
signs in only if it already maps to a user or its provider-verified email is in FFL_SIGNUP_EMAILS.
"""
from __future__ import annotations

import base64
import functools
import hmac
import json
import logging
import os
import time
from typing import Callable
from urllib.parse import parse_qs, urlencode

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client._compat import httpx2 as authlib_httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import db, db_backup

log = logging.getLogger(__name__)

SESSION_COOKIE = "ffl_session"  # the default name; FFL_SESSION_COOKIE overrides it (session_cookie())
OIDC_COOKIE = "ffl_oidc"  # Starlette's signed session: authlib's state and the saved `next`, for the round trip only
OIDC_MAX_AGE = 600
GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_METADATA = APPLE_ISSUER + "/.well-known/openid-configuration"
APPLE_SECRET_DAYS = 7
APPLE_FORM = ("code", "state", "error", "user")  # what the POST callback carries on to the GET
SESSION_DAYS = 90
SETTINGS_PATH = "/settings"
PROVIDERS = ("google", "apple")  # the providers a user signs in with and can link
JOB_HEADER = "x-ffl-job"
# authlib talks through httpx2 when it is installed (it is: the dev group, which `uv sync` installs on the box too),
# whose errors are not httpx's
NET_ERRORS = (httpx.HTTPError, authlib_httpx.HTTPError)

# (method, request path), matched exactly. Everything not named here needs a signed-in session.
PUBLIC: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/health"), ("HEAD", "/health"), ("GET", "/auth/providers"), ("GET", "/auth/google/start"),
    ("GET", "/auth/google/callback"), ("GET", "/auth/apple/start"),
    ("GET", "/auth/apple/callback"), ("POST", "/auth/apple/callback"), ("POST", "/auth/dev"),
    # the browser extension's (ffl/espn_connect.py): each checks its own secret (poll secret, device token)
    ("POST", "/espn/pair/start"), ("POST", "/espn/pair/poll"), ("POST", "/espn/cookies")})
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


def _apple_env() -> tuple[str, str, str, str]:
    """(Services ID, team id, key id, the .p8 key base64'd whole onto one line: env files and SSM take one line)."""
    return tuple(os.environ.get(f"FFL_APPLE_{k}", "") for k in ("CLIENT_ID", "TEAM_ID", "KEY_ID", "PRIVATE_KEY"))


def apple_enabled() -> bool:
    """All four Apple values, the OIDC session's secret and an https FFL_APP_URL (Apple takes https return URLs only)."""
    return bool(all(_apple_env()) and oidc_secret() and cookie_secure())


def apple_client_secret(client_id: str, team_id: str, key_id: str, key_b64: str, now: int) -> str:
    """Apple's client secret: an ES256 JWT signed with the .p8 key (iss the team, sub the Services ID), good for
    APPLE_SECRET_DAYS from `now` (Apple allows six months; the client is rebuilt daily, so it never runs out)."""
    key = ECKey.import_key(base64.b64decode(key_b64))
    claims = {"iss": team_id, "iat": now, "exp": now + APPLE_SECRET_DAYS * 86400, "aud": APPLE_ISSUER,
              "sub": client_id}
    return jwt.encode({"alg": "ES256", "kid": key_id}, claims, key)


@functools.lru_cache(maxsize=4)
def _apple(client_id: str, team_id: str, key_id: str, key_b64: str, day: int):
    """authlib's Apple client, one per day (`day`: days since the epoch) so its JWT secret is always fresh. The secret
    rides in the token request's body (Apple refuses Basic auth), and the answer comes back as a form POST, the only
    mode Apple allows when name and email are asked for (`apple_callback_post`)."""
    secret = apple_client_secret(client_id, team_id, key_id, key_b64, day * 86400)
    return OAuth().register(
        "apple", client_id=client_id, client_secret=secret, server_metadata_url=APPLE_METADATA,
        client_kwargs={"scope": "openid name email", "token_endpoint_auth_method": "client_secret_post"},
        authorize_params={"response_mode": "form_post"})


def _client(provider: str):
    """The provider's authlib client; 404 for one this app does not have, 503 for one not configured here."""
    if provider == "google":
        if not google_enabled():
            raise HTTPException(503, "google_not_configured")
        return _google(*_google_env())
    if provider == "apple":
        if not apple_enabled():
            raise HTTPException(503, "apple_not_configured")
        return _apple(*_apple_env(), int(time.time()) // 86400)
    raise HTTPException(404, "Not Found")


def enabled() -> dict[str, bool]:
    return {"google": google_enabled(), "apple": apple_enabled()}


def _callback_url(provider: str) -> str:
    return os.environ["FFL_APP_URL"].rstrip("/") + f"/api/auth/{provider}/callback"  # _client() checked it


def _set_session(resp, user_id: int, request: Request):
    token = db.create_session(user_id, request.headers.get("user-agent", ""))
    resp.set_cookie(session_cookie(), token, max_age=SESSION_DAYS * 86400, **_cookie_kwargs())
    return resp


def _signin_error(code: str) -> RedirectResponse:
    return RedirectResponse(f"/signin?error={code}", status_code=302)


router = APIRouter()


@router.get("/auth/providers")
def providers():
    return {**enabled(), "dev": dev_signin_enabled()}


@router.get("/auth/{provider}/start")
async def signin_start(provider: str, request: Request, next: str | None = None):
    client = _client(provider)
    request.session["next"] = safe_next(next)  # rides in ffl_oidc to the callback
    request.session.pop("link_user", None)  # an abandoned link's: this round trip signs in
    try:
        return await client.authorize_redirect(request, _callback_url(provider))
    except (OAuthError, *NET_ERRORS, ValueError) as e:  # the provider's metadata unreachable
        log.warning("%s sign-in start failed: %s", provider, getattr(e, "error", None) or type(e).__name__)
        return _signin_error("failed")


def _link_result(code: str) -> RedirectResponse:
    return RedirectResponse(f"{SETTINGS_PATH}?link={code}", status_code=302)


@router.get("/auth/{provider}/link")
async def link_start(provider: str, request: Request):
    """Signed in: the provider's round trip, its identity then linked to this user (ffl-soc.6.2.4), back to Settings.
    The callback is the sign-in's (one redirect URI); `link_user` in the signed oidc cookie tells it apart."""
    user = current_user(request)
    client = _client(provider)
    request.session["link_user"] = user["id"]
    try:
        return await client.authorize_redirect(request, _callback_url(provider))
    except (OAuthError, *NET_ERRORS, ValueError) as e:
        log.warning("%s link start failed: %s", provider, getattr(e, "error", None) or type(e).__name__)
        return _link_result("failed")


def link(user_id: int, provider: str, subject: str, email: str | None) -> str:
    """Map this identity to `user_id`: "linked" (or it was theirs already), "taken" when it signs another user in
    (never moved). The sign-up list does not apply: the user is signed in and has just proved they hold it."""
    uid = db.identity_user(provider, subject)
    if uid is not None:
        return "linked" if uid == user_id else "taken"
    db.link_identity(user_id, provider, subject, email or "")
    return "linked"


def _verified(value) -> bool:
    """An id_token's email_verified: Google sends a boolean, Apple the string "true" as often as not."""
    return value is True or value == "true"


def apple_name(raw: str | None) -> str | None:
    """The name in Apple's `user` form field (JSON, sent on the first authorization only); None when absent or junk."""
    try:
        name = (json.loads(raw) if raw else {}).get("name") or {}
        full = " ".join(str(name.get(k) or "").strip() for k in ("firstName", "lastName")).strip()
    except (ValueError, AttributeError):
        return None
    return full or None


@router.post("/auth/apple/callback")
async def apple_callback_post(request: Request):
    """Apple answers by a form the browser POSTs here from appleid.apple.com: a cross-site POST, which never carries
    the SameSite=Lax ffl_oidc cookie that holds the round trip's state. So this reads the form only, never the
    session, and sends the browser on to the GET callback with it: a cross-site top-level GET does carry Lax cookies,
    and that callback checks state and nonce as Google's does."""
    form = parse_qs((await request.body()).decode("utf-8", "replace"))  # urlencoded: no multipart parser needed
    params = {k: form[k][0] for k in APPLE_FORM if form.get(k) and form[k][0]}
    url = os.environ.get("FFL_APP_URL", "").rstrip("/") + "/api/auth/apple/callback"
    return RedirectResponse(f"{url}?{urlencode(params)}", status_code=303)


@router.get("/auth/{provider}/callback")
async def signin_callback(provider: str, request: Request, user: str | None = None):
    client = _client(provider)
    next_ = safe_next(request.session.pop("next", None))
    link_user = request.session.pop("link_user", None)
    failed = _link_result("failed") if link_user is not None else _signin_error("failed")
    try:
        token = await client.authorize_access_token(request)
    except (OAuthError, JoseError, *NET_ERRORS, ValueError) as e:  # refused, bad state or id_token, no exchange
        log.warning("%s sign-in callback failed: %s", provider, getattr(e, "error", None) or type(e).__name__)
        return failed
    info = (token or {}).get("userinfo") or {}
    if not info.get("sub"):
        return failed
    if link_user is not None:
        if await run_in_threadpool(db.user_row, link_user) is None:  # the account was deleted meanwhile
            return _signin_error("failed")
        return _link_result(await run_in_threadpool(link, link_user, provider, str(info["sub"]), info.get("email")))
    name = info.get("name") if provider == "google" else apple_name(user)  # Apple's id_token carries no name
    uid = await run_in_threadpool(sign_in, provider, str(info["sub"]), info.get("email"),
                                  _verified(info.get("email_verified")), name)
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
    email: str  # the confirmation: the account's own email, typed (any case)


# Called with the user id after an account is deleted (api's: drop their in-memory page caches).
ON_DELETE: list[Callable[[int], None]] = []


@router.post("/auth/delete")
def delete_account(body: DeleteIn, request: Request):
    """The account and every personal row (`db.delete_user`), then signed out."""
    user = current_user(request)
    if body.email.strip().lower() != user["email"].lower():
        raise HTTPException(422, "email_mismatch")
    db.delete_user(user["id"])
    for fn in ON_DELETE:
        try:
            fn(user["id"])
        except Exception:
            log.exception("after deleting user %s", user["id"])
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(session_cookie(), **_cookie_kwargs())
    return resp


@router.get("/account")
def account(request: Request):
    """Settings' Account section: who is signed in, their sessions (this one marked), their sign-ins, and the
    providers configured here that they have not linked yet."""
    user = current_user(request)
    ids = db.user_identities(user["id"])
    on = enabled()
    have = {i["provider"] for i in ids}
    return {"email": user["email"], "name": user["name"],
            "sessions": db.user_sessions(user["id"], current=request.cookies.get(session_cookie())),
            "identities": ids, "linkable": [p for p in PROVIDERS if on.get(p) and p not in have]}


@router.get("/auth/me")
def me(request: Request):
    user = current_user(request)
    return {"email": user["email"], "name": user["name"]}
