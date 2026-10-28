"""Every API route answers 401 without a session; the timers' routes take the job token (and only it); the public
routes need nothing. These run the real middleware (`real_auth`), not conftest's signed-in bypass."""
import re

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from ffl import api, auth, db

pytestmark = pytest.mark.real_auth

TOKEN = "job-token-for-tests"


class _ReachedLive(Exception):
    pass


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FFL_JOB_TOKEN", TOKEN)
    # /live never ends and TestClient waits for the whole body: a stream wrongly reached errors instead of hanging
    def tick(*a, **k):
        raise _ReachedLive
    monkeypatch.setattr(api.live.STATE, "wait_for_tick", tick)


def _session() -> str:
    uid = db.create_user("me@example.com", "Me")
    return db.create_session(uid, "pytest")


def _echo_app() -> FastAPI:
    """The middleware over routes at the guarded paths that answer who they ran as: no handler work, no ESPN."""
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)

    def who(user: dict = Depends(auth.current_user)):
        return {"user": user["email"]}

    def ran(request: Request):
        return {"user": request.scope["state"].get("user")}

    for _, path in auth.JOB_ONLY | auth.JOB_OR_USER:
        app.add_api_route(path, ran, methods=["POST"])
    app.add_api_route("/me", who, methods=["GET"])
    return app


def _client(app=None) -> TestClient:
    return TestClient(app or api.app, raise_server_exceptions=False)


def test_every_route_needs_a_session():
    c = _client()
    checked = []
    for r in api.app.routes:
        if not isinstance(r, APIRoute):
            continue
        path = re.sub(r"{[^}]*}", "1", r.path)
        for m in sorted(r.methods):
            if (m, r.path) in auth.PUBLIC:
                continue
            resp = c.request(m, path)
            # job routes too: no session and no token is signed out (401), the same as any page
            assert resp.status_code == 401, (m, r.path, resp.status_code)
            assert resp.json() == {"error": "signed_out"}, (m, r.path)
            assert resp.headers["cache-control"] == "no-store"
            checked.append((m, r.path))
    assert ("GET", "/week/{n}") in checked and ("POST", "/lineup") in checked
    assert ("POST", "/notify/tick") in checked
    assert len(checked) > 50, len(checked)


def test_a_bad_cookie_is_signed_out():
    c = _client()
    c.cookies.set(auth.SESSION_COOKIE, "not-a-session")
    r = c.get("/week/1")
    assert r.status_code == 401 and r.json() == {"error": "signed_out"}


def test_job_routes_refuse_a_session_without_token():
    c = _client()
    c.cookies.set(auth.SESSION_COOKIE, _session())
    for m, path in auth.JOB_ONLY:
        r = c.request(m, path, headers={auth.JOB_HEADER: "wrong"} if path == "/refresh" else {})
        assert r.status_code == 403, path
        assert r.json() == {"error": "job_only"}
        assert r.headers["cache-control"] == "no-store"


def test_job_routes_take_the_token():
    c = _client(_echo_app())
    for m, path in auth.JOB_ONLY:
        r = c.request(m, path, headers={auth.JOB_HEADER: TOKEN})
        assert r.status_code == 200, path
        assert r.json() == {"user": None}
        assert c.request(m, path, headers={auth.JOB_HEADER: "wrong"}).status_code == 401


def test_job_token_unset_refuses_every_job(monkeypatch):
    monkeypatch.delenv("FFL_JOB_TOKEN")
    for m, path in auth.JOB_ONLY:
        assert auth.decide(m, path, None, "") == (401, None)
        assert auth.decide(m, path, None, TOKEN) == (401, None)


def test_job_token_is_not_a_session():
    """The timers' token opens the job routes only, never a page."""
    assert auth.decide("GET", "/week/1", None, TOKEN) == (401, None)
    assert auth.decide("POST", "/lineup", None, TOKEN) == (401, None)


def test_outlook_refresh_takes_either():
    c = _client(_echo_app())
    assert c.post("/outlook/refresh", headers={auth.JOB_HEADER: TOKEN}).json() == {"user": None}
    assert c.post("/outlook/refresh").status_code == 401
    c.cookies.set(auth.SESSION_COOKIE, _session())
    r = c.post("/outlook/refresh")
    assert r.status_code == 200 and r.json()["user"]["email"] == "me@example.com"


def test_current_user_reads_the_session():
    c = _client(_echo_app())
    assert c.get("/me").status_code == 401
    c.cookies.set(auth.SESSION_COOKIE, _session())
    assert c.get("/me").json() == {"user": "me@example.com"}


def test_decide_passes_the_user():
    token = _session()
    code, user = auth.decide("GET", "/roster", token, None)
    assert code == 0 and user["email"] == "me@example.com"


def test_live_stream_passes_with_session():
    """Raw ASGI, stopped at the first body chunk: TestClient waits for a whole body, and /live has none."""
    import anyio
    token = _session()
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
        with pytest.raises(BaseException) as e:
            await api.app(scope, receive, send)
        assert e.group_contains(_FirstBytes) if isinstance(e.value, BaseExceptionGroup) else e.type is _FirstBytes
    anyio.run(main)
    start = sent[0]
    assert start["status"] == 200
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert "no-transform" in headers["cache-control"]
    assert sent[-1]["body"].decode().startswith("retry: 3000\ndata: ")


def test_public_routes_need_nothing(monkeypatch):
    for k in ("FFL_AUTH_SECRET", "FFL_GOOGLE_CLIENT_ID", "FFL_GOOGLE_CLIENT_SECRET", "FFL_DEV_SIGNIN"):
        monkeypatch.delenv(k, raising=False)
    c = TestClient(api.app, raise_server_exceptions=False, follow_redirects=False)
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/auth/providers").json() == {"google": False, "apple": False, "dev": False}
    assert c.get("/auth/google/start").status_code == 503  # unconfigured, and nothing reaches Google
    assert c.get("/auth/google/callback").status_code == 503
    assert c.get("/auth/apple/start").status_code == 503
    assert c.get("/auth/apple/callback").status_code == 503
    r = c.post("/auth/dev", json={"email": "me@example.com"})
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}  # off: as if no such route
    for m, path in auth.PUBLIC:
        r = c.request(m, path)
        assert r.status_code not in (401, 403), (m, path)


def test_the_tables_are_the_briefs():
    assert auth.PUBLIC == {("GET", "/health"), ("HEAD", "/health"), ("GET", "/auth/providers"),
                           ("GET", "/auth/google/start"), ("GET", "/auth/google/callback"),
                           ("GET", "/auth/apple/start"), ("GET", "/auth/apple/callback"),
                           ("POST", "/auth/apple/callback"), ("POST", "/auth/dev")}
    assert auth.JOB_ONLY == {("POST", "/notify/tick"), ("POST", "/news/run"), ("POST", "/transactions/sync"),
                             ("POST", "/refresh")}
    assert auth.JOB_OR_USER == {("POST", "/outlook/refresh")}


def test_head_health_is_public():
    """Uptime checks send HEAD: it passes signed out and answers 200."""
    assert _client().head("/health").status_code == 200


def test_public_routes_skip_the_threadpool(monkeypatch):
    """A public request is passed on the loop, without decide: a threadpool full of stalled session reads never
    holds up /health."""
    def boom(*a, **k):
        raise AssertionError("decide called")
    monkeypatch.setattr(auth, "decide", boom)
    c = _client()
    assert c.get("/health").json() == {"ok": True}
    assert c.head("/health").status_code == 200


def test_middleware_decides_off_the_event_loop(monkeypatch):
    """decide reads the database (sync): it runs in the threadpool, never on the loop thread, and a slide it reports
    still reaches the response."""
    import threading
    seen = {}
    real = auth.decide

    def spy(*a, **k):
        seen["decide"] = threading.get_ident()
        return real(*a, **k)
    monkeypatch.setattr(auth, "decide", spy)
    app = _echo_app()

    @app.get("/loop")
    async def loop_thread():
        seen["loop"] = threading.get_ident()
        return {}
    c = _client(app)
    token = _session()
    _age(token)
    c.cookies.set(auth.SESSION_COOKIE, token)
    r = c.get("/loop")
    assert r.status_code == 200
    assert seen["decide"] != seen["loop"]
    assert any(h.startswith(auth.SESSION_COOKIE + "=") for h in r.headers.get_list("set-cookie"))


def _age(token):
    import hashlib
    from datetime import datetime, timedelta, timezone

    import sqlalchemy as sa
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    with db._write() as c:
        c.execute(sa.update(db.sessions).where(
            db.sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()).values(last_seen=old))
