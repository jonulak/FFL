"""The sign-in routes: Google's callback (authlib patched, nothing reaches Google), the dev sign-in, sign-out,
/auth/me and /auth/providers, through the real middleware (`real_auth`)."""
import pytest
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from ffl import api, auth, db

pytestmark = pytest.mark.real_auth

CID, CSECRET = "client-id-for-tests", "client-secret-for-tests"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("FFL_DEV_SIGNIN", "FFL_SIGNUP_EMAILS", "FFL_APP_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FFL_GOOGLE_CLIENT_ID", CID)
    monkeypatch.setenv("FFL_GOOGLE_CLIENT_SECRET", CSECRET)
    monkeypatch.setenv("FFL_AUTH_SECRET", "auth-secret-for-tests")
    monkeypatch.setenv("FFL_APP_URL", "http://ffl.test")
    auth._local.cache_clear()
    yield
    auth._local.cache_clear()


def _client() -> TestClient:
    return TestClient(api.app, raise_server_exceptions=False, follow_redirects=False)


def _google(monkeypatch, userinfo: dict | None = None, error: Exception | None = None):
    """authlib's client with both network steps replaced: start redirects to a fake Google, the callback answers
    `userinfo` (or raises `error`)."""
    g = auth._google(CID, CSECRET)

    async def redirect(request, redirect_uri=None, **kw):
        assert redirect_uri == "http://ffl.test/api/auth/google/callback"
        return RedirectResponse("https://accounts.google.test/o/oauth2/auth", status_code=302)

    async def token(request, **kw):
        if error:
            raise error
        return {"userinfo": userinfo}
    monkeypatch.setattr(g, "authorize_redirect", redirect)
    monkeypatch.setattr(g, "authorize_access_token", token)


def _cookie_header(r) -> str:
    sets = [h for h in r.headers.get_list("set-cookie") if h.startswith(auth.session_cookie() + "=")]
    assert len(sets) == 1, "one session Set-Cookie"
    return sets[0]


def _flow(c: TestClient, next_: str = "/roster"):
    r = c.get("/auth/google/start", params={"next": next_})
    assert r.status_code == 302 and r.headers["location"].startswith("https://accounts.google.test/")
    assert any(h.startswith("ffl_oidc=") for h in r.headers.get_list("set-cookie"))
    return c.get("/auth/google/callback", params={"code": "c", "state": "s"})


INFO = {"sub": "g-123", "email": "Me@Example.com", "email_verified": True, "name": "Me"}


def test_google_callback_signs_in_listed_email(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    _google(monkeypatch, INFO)
    c = _client()
    r = _flow(c, "/players?pos=WR")
    assert r.status_code == 302 and r.headers["location"] == "/players?pos=WR"
    h = _cookie_header(r).lower()
    assert "httponly" in h and "samesite=lax" in h and "path=/" in h
    assert f"max-age={auth.SESSION_DAYS * 86400}" in h
    assert db.identity_user("google", "g-123") is not None
    assert c.get("/auth/me").json() == {"email": "Me@Example.com", "name": "Me"}


def test_google_callback_refuses_unlisted(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "someone@else.com")
    _google(monkeypatch, INFO)
    r = _flow(_client())
    assert r.status_code == 302 and r.headers["location"] == "/signin?error=closed"
    assert not any(h.startswith(auth.SESSION_COOKIE + "=") for h in r.headers.get_list("set-cookie"))
    assert db.user_by_email("me@example.com") is None


def test_google_callback_refuses_unverified(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    _google(monkeypatch, {**INFO, "email_verified": False})
    assert _flow(_client()).headers["location"] == "/signin?error=closed"
    assert db.user_by_email("me@example.com") is None


def test_google_callback_provider_error(monkeypatch):
    from authlib.integrations.base_client import OAuthError
    _google(monkeypatch, error=OAuthError(error="access_denied"))
    r = _flow(_client())
    assert r.status_code == 302 and r.headers["location"] == "/signin?error=failed"


def test_google_callback_without_sub_fails(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    _google(monkeypatch, {"email": "me@example.com", "email_verified": True})
    assert _flow(_client()).headers["location"] == "/signin?error=failed"


def test_google_routes_503_without_auth_secret(monkeypatch):
    monkeypatch.delenv("FFL_AUTH_SECRET")
    c = _client()
    assert c.get("/auth/google/start").status_code == 503
    assert c.get("/auth/google/callback").status_code == 503


def test_cookie_secure_follows_app_url(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    _google(monkeypatch, INFO)
    monkeypatch.setenv("FFL_APP_URL", "https://ffl.test")
    g = auth._google(CID, CSECRET)

    async def redirect(request, redirect_uri=None, **kw):
        assert redirect_uri == "https://ffl.test/api/auth/google/callback"
        return RedirectResponse("https://accounts.google.test/o/oauth2/auth", status_code=302)
    monkeypatch.setattr(g, "authorize_redirect", redirect)
    assert "secure" in _cookie_header(_flow(_client())).lower().split("; ")
    monkeypatch.setenv("FFL_APP_URL", "http://ffl.test")
    _google(monkeypatch, INFO)
    assert "secure" not in _cookie_header(_flow(_client())).lower().split("; ")


def test_next_is_same_origin_only(monkeypatch):
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "evil.com", "", None, "\\\\evil.com",
                "/\t/evil.com", "/\n/evil.com", "/\r\n/evil.com", "/a\\b", "/x\x00", "/x\x1f"):
        assert auth.safe_next(bad) == "/", bad
    assert auth.safe_next("/roster?league=espn:1") == "/roster?league=espn:1"
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    _google(monkeypatch, INFO)
    assert _flow(_client(), "//evil.com").headers["location"] == "/"


def test_dev_signin_404_off_local(monkeypatch):
    c = _client()
    r = c.post("/auth/dev", json={"email": "me@example.com"})  # FFL_DEV_SIGNIN unset
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    monkeypatch.setenv("FFL_DEV_SIGNIN", "1")
    monkeypatch.setattr(auth.db_backup, "is_local", lambda url: False)  # an RDS
    auth._local.cache_clear()
    r = c.post("/auth/dev", json={"email": "me@example.com"})
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert c.post("/auth/dev").status_code == 404  # no body: still 404, never 422
    assert db.user_by_email("me@example.com") is None


def test_dev_signin_sets_cookie(monkeypatch):
    monkeypatch.setenv("FFL_DEV_SIGNIN", "1")
    c = _client()
    r = c.post("/auth/dev", json={"email": "dev@example.com"})  # not on FFL_SIGNUP_EMAILS: dev ignores it
    assert r.status_code == 200 and r.json() == {"ok": True}
    h = _cookie_header(r).lower()
    assert "httponly" in h and "samesite=lax" in h and "path=/" in h
    assert c.get("/auth/me").json()["email"] == "dev@example.com"
    # the same email again signs in as the same user
    uid = db.user_by_email("dev@example.com")["id"]
    _client().post("/auth/dev", json={"email": "dev@example.com"})
    assert db.user_by_email("dev@example.com")["id"] == uid
    assert c.post("/auth/dev", json={}).status_code == 422


def test_session_cookie_name_follows_env(monkeypatch):
    """Two test servers on one host share cookies across ports: each names its own (FFL_SESSION_COOKIE)."""
    monkeypatch.setenv("FFL_DEV_SIGNIN", "1")
    monkeypatch.setenv("FFL_SESSION_COOKIE", "ffl_session_3302")
    c = _client()
    r = c.post("/auth/dev", json={"email": "dev@example.com"})
    assert _cookie_header(r).startswith("ffl_session_3302=")
    assert c.get("/auth/me").json()["email"] == "dev@example.com"
    other = _client()
    other.cookies.set("ffl_session", c.cookies.get("ffl_session_3302"))  # the default name is not read
    assert other.get("/auth/me").status_code == 401
    r = c.post("/auth/signout")
    assert _cookie_header(r).startswith("ffl_session_3302=")


def test_signout_kills_session(monkeypatch):
    monkeypatch.setenv("FFL_DEV_SIGNIN", "1")
    c = _client()
    c.post("/auth/dev", json={"email": "dev@example.com"})
    token = c.cookies.get(auth.SESSION_COOKIE)
    r = c.post("/auth/signout")
    assert r.status_code == 200
    h = _cookie_header(r).lower()
    assert "max-age=0" in h and "httponly" in h and "samesite=lax" in h and "path=/" in h
    assert db.session_user(token) is None
    c.cookies.set(auth.SESSION_COOKIE, token)  # a kept copy of the old cookie is dead too
    r = c.get("/week/1")
    assert r.status_code == 401 and r.json() == {"error": "signed_out"}


def test_me_and_signout_signed_out():
    c = _client()
    for m, path in (("GET", "/auth/me"), ("POST", "/auth/signout")):
        r = c.request(m, path)
        assert r.status_code == 401 and r.json() == {"error": "signed_out"}


def test_current_user_401_matches_middleware():
    """A dependency's refusal answers like the middleware's: a job-token request reaching a user-only handler."""
    import fastapi
    from fastapi import Depends
    app = fastapi.FastAPI()
    app.add_exception_handler(auth.SignedOut, auth.signed_out_handler)

    @app.get("/x")
    def x(user: dict = Depends(auth.current_user)):
        return user
    r = TestClient(app).get("/x")
    assert r.status_code == 401 and r.json() == {"error": "signed_out"}
    assert r.headers["cache-control"] == "no-store"


def test_providers_flags(monkeypatch):
    c = _client()
    assert c.get("/auth/providers").json() == {"google": True, "apple": False, "dev": False}
    monkeypatch.setenv("FFL_DEV_SIGNIN", "1")
    assert c.get("/auth/providers").json() == {"google": True, "apple": False, "dev": True}
    for k, v in (("FFL_GOOGLE_CLIENT_ID", CID), ("FFL_GOOGLE_CLIENT_SECRET", CSECRET),
                 ("FFL_AUTH_SECRET", "auth-secret-for-tests")):
        monkeypatch.setenv(k, "")  # empty is unset
        assert c.get("/auth/providers").json()["google"] is False, k
        monkeypatch.delenv(k)
        assert c.get("/auth/providers").json()["google"] is False, k
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("FFL_APP_URL")  # no origin for the callback: start would 503, so say no
    assert c.get("/auth/providers").json()["google"] is False
    assert c.get("/auth/google/start").status_code == 503


def test_google_callback_bad_id_token(monkeypatch):
    from joserfc.errors import BadSignatureError
    _google(monkeypatch, error=BadSignatureError())
    r = _flow(_client())
    assert r.status_code == 302 and r.headers["location"] == "/signin?error=failed"


def test_google_callback_real_state_check_without_oidc_cookie(monkeypatch):
    """authlib unpatched: no ffl_oidc cookie means no saved state, refused before any network call."""
    import httpx

    def no_network(*a, **k):
        raise AssertionError("reached the network")
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    r = _client().get("/auth/google/callback", params={"code": "c", "state": "x"})
    assert r.status_code == 302 and r.headers["location"] == "/signin?error=failed"
    assert not any(h.startswith(auth.SESSION_COOKIE + "=") for h in r.headers.get_list("set-cookie"))
    with db._read() as conn:
        assert conn.execute(db.sa.select(db.sa.func.count()).select_from(db.users)
                            .where(db.users.c.id != 0)).scalar() == 0  # the template's test user only


def _age_session(token: str, hours: float):
    import hashlib
    from datetime import datetime, timedelta, timezone
    seen = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with db._write() as conn:
        conn.execute(db.sa.update(db.sessions).where(
            db.sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()).values(last_seen=seen))


def _signed_in_client() -> tuple[TestClient, str]:
    c = _client()
    uid = db.create_user("me@example.com", "Me")
    token = db.create_session(uid, "pytest")
    c.cookies.set(auth.SESSION_COOKIE, token)
    return c, token


def test_session_cookie_slides_with_the_session(monkeypatch):
    monkeypatch.setenv("FFL_APP_URL", "https://ffl.test")
    c, token = _signed_in_client()
    r = c.get("/auth/me")  # seen just now: no slide, no cookie
    assert r.status_code == 200 and not r.headers.get_list("set-cookie")
    _age_session(token, 2)
    r = c.get("/auth/me")
    assert r.status_code == 200
    h = _cookie_header(r)
    assert h.startswith(f"{auth.SESSION_COOKIE}={token};"), "the same session re-sent"
    attrs = h.lower().split("; ")
    assert f"max-age={auth.SESSION_DAYS * 86400}" in attrs
    assert {"httponly", "samesite=lax", "path=/", "secure"} <= set(attrs)
    assert not c.get("/auth/me").headers.get_list("set-cookie")  # slid: within the hour again


def test_signout_on_a_slid_session_only_expires(monkeypatch):
    c, token = _signed_in_client()
    _age_session(token, 2)
    r = c.post("/auth/signout")
    assert r.status_code == 200
    assert "max-age=0" in _cookie_header(r).lower()  # the route's expiry, not a slide re-sending the session


def test_slid_session_keeps_live_stream():
    """/live through the slide's wrapped send: the cookie rides on the start, the stream still streams."""
    import anyio
    _, token = _signed_in_client()
    _age_session(token, 2)
    sent: list[dict] = []

    class _FirstBytes(Exception):
        pass

    async def receive():
        await anyio.sleep_forever()

    async def send(msg):
        sent.append(msg)
        if msg["type"] == "http.response.body" and msg.get("body"):
            raise _FirstBytes

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/live", "raw_path": b"/live", "root_path": "", "query_string": b"", "server": ("t", 80),
             "client": ("t", 1), "headers": [(b"host", b"t"), (b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())]}

    async def main():
        with pytest.raises(BaseException):
            await api.app(scope, receive, send)
    anyio.run(main)
    start = sent[0]
    assert start["status"] == 200
    headers = [(k.decode(), v.decode()) for k, v in start["headers"]]
    assert any(k == "content-type" and v.startswith("text/event-stream") for k, v in headers)
    assert any(k == "set-cookie" and v.startswith(auth.SESSION_COOKIE + "=") for k, v in headers)
    assert sent[-1]["body"].decode().startswith("retry: 3000\ndata: ")


def test_middleware_order():
    """AuthMiddleware decides before the OIDC session middleware reads anything."""
    assert api.app.user_middleware[0].cls is auth.AuthMiddleware
