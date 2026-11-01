"""The sign-in routes: first-run setup, username + password sign-in, password change, sign-out and /auth/me, through
the real middleware (`real_auth`)."""
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ffl import appconf, api, auth, db, espn_connect, leagues, passwords
from tests.conftest import TEST_USER

pytestmark = pytest.mark.real_auth

PW = "12345678"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    appconf.put(appconf.APP_URL, None)
    monkeypatch.setattr(auth, "SIGNIN_WINDOW", espn_connect.Window(10, 900))  # each test its own window


def _client() -> TestClient:
    return TestClient(api.app, raise_server_exceptions=False, follow_redirects=False)


def _cookie_header(r) -> str:
    sets = [h for h in r.headers.get_list("set-cookie") if h.startswith(auth.session_cookie() + "=")]
    assert len(sets) == 1, "one session Set-Cookie"
    return sets[0]


def _user(username: str = "jon", password: str = PW, email: str | None = None) -> int:
    uid = db.create_user(email, None)
    db.set_username(uid, username)
    db.set_password(uid, passwords.hash_password(password))
    return uid


def _users() -> int:
    with db._read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(db.users)).scalar_one()


# --- first-run setup ---

def test_setup_claims_the_passwordless_test_user_once():
    c = _client()
    assert c.get("/setup/status").json() == {"needs_setup": True}
    before = _users()
    r = c.post("/setup", json={"username": "jon", "password": PW})
    assert r.status_code == 200
    h = _cookie_header(r).lower()
    assert "httponly" in h and "samesite=lax" in h and "path=/" in h
    assert f"max-age={auth.SESSION_DAYS * 86400}" in h
    assert _users() == before, "the existing row is claimed, no new one"
    row = db.user_by_username("jon")
    assert row["id"] == TEST_USER["id"] and row["is_admin"] is True
    assert c.get("/auth/me").json()["username"] == "jon"
    assert c.post("/setup", json={"username": "amy", "password": PW}).status_code == 404
    assert c.get("/setup/status").json() == {"needs_setup": False}


def test_setup_on_an_empty_users_table_makes_the_admin():
    with db._write() as c:
        c.execute(sa.delete(db.users))
    r = _client().post("/setup", json={"username": "jon", "password": PW})
    assert r.status_code == 200
    assert db.user_by_username("jon")["is_admin"] is True and _users() == 1


def test_setup_checks_the_username_and_password():
    c = _client()
    r = c.post("/setup", json={"username": "a b", "password": PW})
    assert r.status_code == 422 and r.json() == {"error": "bad_username"}
    r = c.post("/setup", json={"username": "jon", "password": "1234567"})
    assert r.status_code == 422 and r.json() == {"error": "short_password"}
    assert c.post("/setup", json={"username": "jon", "password": "x" * 1025}).status_code == 422
    assert c.get("/setup/status").json() == {"needs_setup": True}


def test_setup_answers_a_taken_username_without_the_detail():
    uid = db.create_user(None, None)
    db.set_username(uid, "jon")  # a username, no password: setup still open, and the oldest user is someone else
    r = _client().post("/setup", json={"username": "JON", "password": PW})
    assert r.status_code == 409 and r.json() == {"error": "username_taken"}


# --- sign-in ---

def test_signin_right_password_sets_the_session():
    _user("jon")
    c = _client()
    r = c.post("/auth/signin", json={"username": "Jon", "password": PW})  # any case
    assert r.status_code == 200
    _cookie_header(r)
    me = c.get("/auth/me").json()
    assert me["username"] == "jon" and me["is_admin"] is False


def test_signin_wrong_password_and_unknown_user_answer_alike():
    _user("jon")
    c = _client()
    for body in ({"username": "jon", "password": "wrong-pass"}, {"username": "nobody", "password": PW}):
        r = c.post("/auth/signin", json=body)
        assert r.status_code == 401 and r.json() == {"error": "bad_login"}, body
        assert not r.headers.get_list("set-cookie")


def test_an_invite_without_a_password_cannot_sign_in():
    uid = db.create_invite("amy@example.com", "Amy")
    db.set_username(uid, "amy")
    r = _client().post("/auth/signin", json={"username": "amy", "password": PW})
    assert r.status_code == 401 and r.json() == {"error": "bad_login"}


def test_signin_rate_limits_wrong_tries():
    _user("jon")
    c = _client()
    for _ in range(10):
        assert c.post("/auth/signin", json={"username": "jon", "password": "wrong-pass"}).status_code == 401
    r = c.post("/auth/signin", json={"username": "JON", "password": PW})
    assert r.status_code == 429 and r.json() == {"error": "rate_limited"}


def test_a_sign_in_never_makes_a_membership():
    uid = _user("jon")
    assert _client().post("/auth/signin", json={"username": "jon", "password": PW}).status_code == 200
    assert leagues.for_user(uid) == []


# --- password change ---

def test_change_password():
    _user("jon")
    c = _client()
    c.post("/auth/signin", json={"username": "jon", "password": PW})
    r = c.post("/auth/password", json={"current": "wrong-pass", "new": "abcdefgh"})
    assert r.status_code == 401 and r.json() == {"error": "bad_login"}
    r = c.post("/auth/password", json={"current": PW, "new": "short"})
    assert r.status_code == 422 and r.json() == {"error": "short_password"}
    assert c.post("/auth/password", json={"current": PW, "new": "abcdefgh"}).status_code == 200
    assert c.get("/auth/me").status_code == 200, "this session stays"
    assert _client().post("/auth/signin", json={"username": "jon", "password": PW}).status_code == 401
    assert _client().post("/auth/signin", json={"username": "jon", "password": "abcdefgh"}).status_code == 200


def test_change_password_needs_a_session():
    r = _client().post("/auth/password", json={"current": PW, "new": "abcdefgh"})
    assert r.status_code == 401 and r.json() == {"error": "signed_out"}


# --- sessions ---

def test_cookie_secure_follows_app_url(monkeypatch):
    _user("jon")
    body = {"username": "jon", "password": PW}
    appconf.put(appconf.APP_URL, "https://ffl.test")
    assert "secure" in _cookie_header(_client().post("/auth/signin", json=body)).lower().split("; ")
    appconf.put(appconf.APP_URL, "http://ffl.test")
    assert "secure" not in _cookie_header(_client().post("/auth/signin", json=body)).lower().split("; ")


def test_next_is_same_origin_only():
    for bad in ("//evil.com", "https://evil.com", "/\\evil.com", "evil.com", "", None, "\\\\evil.com",
                "/\t/evil.com", "/\n/evil.com", "/\r\n/evil.com", "/a\\b", "/x\x00", "/x\x1f"):
        assert auth.safe_next(bad) == "/", bad
    assert auth.safe_next("/roster?league=espn:1") == "/roster?league=espn:1"


def test_session_cookie_name_follows_env(monkeypatch):
    """Two test servers on one host share cookies across ports: each names its own (FFL_SESSION_COOKIE)."""
    _user("jon")
    monkeypatch.setenv("FFL_SESSION_COOKIE", "ffl_session_3302")
    c = _client()
    r = c.post("/auth/signin", json={"username": "jon", "password": PW})
    assert _cookie_header(r).startswith("ffl_session_3302=")
    assert c.get("/auth/me").json()["username"] == "jon"
    other = _client()
    other.cookies.set("ffl_session", c.cookies.get("ffl_session_3302"))  # the default name is not read
    assert other.get("/auth/me").status_code == 401
    r = c.post("/auth/signout")
    assert _cookie_header(r).startswith("ffl_session_3302=")


def test_signout_kills_session():
    _user("jon")
    c = _client()
    c.post("/auth/signin", json={"username": "jon", "password": PW})
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
    appconf.put(appconf.APP_URL, "https://ffl.test")
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
    """AuthMiddleware decides before anything else; only CORS sits outside it: the browser extension's preflight is
    answered before the session check, for extension origins and no credentials."""
    from starlette.middleware.cors import CORSMiddleware
    assert [m.cls for m in api.app.user_middleware] == [CORSMiddleware, auth.AuthMiddleware]
    assert api.app.user_middleware[0].kwargs["allow_credentials"] is False
