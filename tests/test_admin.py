"""Admins: inviting users, set-password links, promoting and deleting. The admin routes run as conftest's signed-in
TEST_USER (made admin per test); the setup link is public and runs the real middleware."""
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ffl import api, auth, db, passwords
from tests.conftest import TEST_USER

PW = "12345678"
ME = TEST_USER["id"]


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setenv("FFL_APP_URL", "http://ffl.example/")
    db.set_admin(ME, True)
    return TestClient(api.app, raise_server_exceptions=False, follow_redirects=False)


def _token(link: str) -> str:
    assert link.startswith("http://ffl.example/setup/")
    return link.rsplit("/", 1)[1]


def test_invite_answers_a_setup_link(c):
    r = c.post("/admin/users", json={"email": "a@x", "name": "A"})
    assert r.status_code == 200
    body = r.json()
    assert db.user_by_setup_token(_token(body["link"]))["id"] == body["id"]
    r = c.post("/admin/users", json={"email": "A@x", "name": "B"})
    assert r.status_code == 409 and r.json() == {"error": "email_taken"}


def test_a_non_admin_is_refused(c):
    db.set_admin(ME, False)
    for method, path, body in (("GET", "/admin/users", None), ("POST", "/admin/users", {"email": "a@x"}),
                               ("POST", f"/admin/users/{ME}/reset", None), ("GET", "/llm/usage", None)):
        r = c.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "not_admin", path


@pytest.mark.real_auth
def test_setup_link_sets_the_login_once_and_signs_in(c):
    uid = db.create_invite("a@x", "A")
    token = db.issue_setup_token(uid)
    assert c.post(f"/auth/setup/{token}", json={"username": "a", "password": PW}).json() == {"error": "bad_username"}
    assert c.post(f"/auth/setup/{token}", json={"username": "amy", "password": "x"}).json() == {
        "error": "short_password"}
    r = c.post(f"/auth/setup/{token}", json={"username": "amy", "password": PW})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert c.get("/auth/me").json()["username"] == "amy", "the link signs them in"
    row = db.user_row(uid)
    assert passwords.verify(PW, row["password_hash"]) and row["setup_token_hash"] is None
    again = c.post(f"/auth/setup/{token}", json={"username": "amy", "password": PW})
    assert again.status_code == 404 and again.json() == {"error": "bad_token"}


@pytest.mark.real_auth
def test_an_expired_or_taken_setup_link(c):
    db.set_username(ME, "jon")
    uid = db.create_invite("a@x", "A")
    token = db.issue_setup_token(uid)
    r = c.post(f"/auth/setup/{token}", json={"username": "JON", "password": PW})
    assert r.status_code == 409 and r.json() == {"error": "username_taken"}
    with db.engine().begin() as conn:
        conn.execute(sa.update(db.users).where(db.users.c.id == uid).values(setup_expires="2000-01-01T00:00:00+00:00"))
    r = c.post(f"/auth/setup/{token}", json={"username": "amy", "password": PW})
    assert r.status_code == 404 and r.json() == {"error": "bad_token"}


def test_reset_issues_a_new_link_for_an_existing_user(c):
    uid = db.create_invite("a@x", "A")
    db.set_password(uid, passwords.hash_password("old-password"))
    old = db.issue_setup_token(uid)
    body = c.post(f"/admin/users/{uid}/reset").json()
    assert body["id"] == uid and db.user_by_setup_token(_token(body["link"]))["id"] == uid
    assert db.user_by_setup_token(old) is None, "the new link replaces the old"
    assert c.post("/admin/users/9999/reset").status_code == 404


def test_promote_and_demote_but_never_the_last_admin(c):
    uid = db.create_invite("a@x", "A")
    r = c.post(f"/admin/users/{ME}/admin", json={"is_admin": False})
    assert r.status_code == 409 and r.json() == {"error": "last_admin"}
    assert c.post("/admin/users/9999/admin", json={"is_admin": True}).status_code == 404
    assert c.post(f"/admin/users/{uid}/admin", json={"is_admin": True}).json() == {"ok": True}
    assert c.post(f"/admin/users/{ME}/admin", json={"is_admin": False}).json() == {"ok": True}
    assert db.admin_count() == 1 and db.user_row(uid)["is_admin"] is True


def test_delete_reuses_delete_user_and_fires_on_delete(c, monkeypatch):
    uid = db.create_invite("a@x", "A")
    gone = []
    monkeypatch.setattr(auth, "ON_DELETE", [gone.append])
    r = c.post(f"/admin/users/{ME}/delete")
    assert r.status_code == 409 and r.json() == {"error": "self"}
    assert c.post(f"/admin/users/{uid}/delete").json() == {"ok": True}
    assert db.user_row(uid) is None and gone == [uid]
    assert c.post(f"/admin/users/{uid}/delete").status_code == 404


def test_list_shows_who_has_a_password(c):
    uid = db.create_invite("a@x", "A")
    rows = {u["id"]: u for u in c.get("/admin/users").json()["users"]}
    assert rows[uid]["has_password"] is False and rows[ME]["is_admin"] is True
    assert "password_hash" not in rows[uid]
