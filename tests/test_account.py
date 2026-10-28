"""Settings' Account section (ffl-soc.6.2.4): sessions listed and revoked, sign out everywhere else, a second sign-in
linked, the account deleted with every personal row. Through the real middleware (`real_auth`): a revoked session
must 401 there."""
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from ffl import api, auth, db, leagues, notify
from tests import two_users
from tests.home import HOME_KEY
from tests.test_auth_flow import CID, CSECRET

pytestmark = pytest.mark.real_auth


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FFL_GOOGLE_CLIENT_ID", CID)
    monkeypatch.setenv("FFL_GOOGLE_CLIENT_SECRET", CSECRET)
    monkeypatch.setenv("FFL_AUTH_SECRET", "auth-secret-for-tests")
    monkeypatch.setenv("FFL_APP_URL", "http://ffl.test")
    monkeypatch.setenv("FFL_JOB_TOKEN", "job-token-for-tests")
    monkeypatch.delenv("FFL_SIGNUP_EMAILS", raising=False)


def _signed_in(user_id: int, ua: str = "pytest") -> TestClient:
    c = TestClient(api.app, raise_server_exceptions=False, follow_redirects=False)
    c.cookies.set(auth.session_cookie(), db.create_session(user_id, ua))
    return c


def test_account_lists_sessions_and_marks_this_one():
    uid = db.create_user("me@example.com", "Me")
    other = db.create_session(uid, "iPhone Safari")
    c = _signed_in(uid, "Mac Chrome")
    db.create_session(db.create_user("else@example.com", None), "theirs")  # someone else's never shows
    body = c.get("/account").json()
    assert body["email"] == "me@example.com"
    assert sorted((s["ua"], s["current"]) for s in body["sessions"]) == [("Mac Chrome", True), ("iPhone Safari", False)]
    assert all(len(s["id"]) == 64 for s in body["sessions"])
    assert db.session_user(other) is not None


def test_a_revoked_session_401s():
    uid = db.create_user("me@example.com", "Me")
    phone = _signed_in(uid, "phone")
    desk = _signed_in(uid, "desk")
    phone_id = next(s["id"] for s in desk.get("/account").json()["sessions"] if s["ua"] == "phone")
    r = desk.post(f"/auth/sessions/{phone_id}/revoke")
    assert r.status_code == 200 and r.json() == {"ok": True, "signed_out": False}
    assert phone.get("/account").status_code == 401
    assert desk.get("/account").status_code == 200
    assert desk.post(f"/auth/sessions/{phone_id}/revoke").status_code == 404


def test_revoke_never_reaches_another_users_session():
    me = _signed_in(db.create_user("me@example.com", "Me"))
    them = _signed_in(db.create_user("them@example.com", None))
    their_id = them.get("/account").json()["sessions"][0]["id"]
    assert me.post(f"/auth/sessions/{their_id}/revoke").status_code == 404
    assert them.get("/account").status_code == 200


def test_revoking_this_session_signs_out_here():
    c = _signed_in(db.create_user("me@example.com", "Me"))
    sid = c.get("/account").json()["sessions"][0]["id"]
    r = c.post(f"/auth/sessions/{sid}/revoke")
    assert r.json()["signed_out"] is True
    assert any(h.startswith(auth.session_cookie() + '=""') or "max-age=0" in h.lower()
               for h in r.headers.get_list("set-cookie"))
    assert c.get("/account").status_code == 401


def test_signout_others_keeps_this_one():
    uid = db.create_user("me@example.com", "Me")
    others = [_signed_in(uid, f"d{i}") for i in range(2)]
    here = _signed_in(uid, "here")
    assert here.post("/auth/signout-others").json() == {"ended": 2}
    assert [o.get("/account").status_code for o in others] == [401, 401]
    assert [s["ua"] for s in here.get("/account").json()["sessions"]] == ["here"]


def test_account_routes_refuse_the_job_token():
    c = TestClient(api.app, raise_server_exceptions=False)
    h = {auth.JOB_HEADER: "job-token-for-tests"}
    for m, path in (("GET", "/account"), ("POST", "/auth/delete"), ("POST", "/auth/signout-others"),
                    ("POST", "/auth/sessions/x/revoke"), ("GET", "/auth/google/link")):
        assert c.request(m, path, headers=h, json={"email": "x"}).status_code == 401, path


# --- deleting the account ---

def _owned(user_id: int) -> dict[str, int]:
    """Rows carrying `user_id` in every table that has the column, plus the users row."""
    out = {}
    with db.engine().connect() as c:
        for t in db.metadata.sorted_tables:
            col = t.c.get("user_id") if t.name != "users" else t.c.id
            if col is None:
                continue
            n = c.execute(sa.select(sa.func.count()).select_from(t).where(col == user_id)).scalar_one()
            if n:
                out[t.name] = n
    return out


def _team_rows(league: str, team_id: int) -> int:
    with db.engine().connect() as c:
        return sum(c.execute(sa.select(sa.func.count()).select_from(t).where(
            t.c.league == league, t.c.team_id == team_id)).scalar_one()
            for t in (db.claim_plans, db.seats, db.team_state))


def _fill(ref: leagues.LeagueRef) -> None:
    """One of every personal row for `ref`'s member, and their team's shared rows."""
    db.put_state("prefs", {"quiet": [22, 7]}, user_id=ref.user_id)
    db.put_credentials(ref.user_id, "s2", f"{{SWID-{ref.user_id}}}")
    db.put_league_state("notify_prefs", {"moves": False}, ref=ref)
    db.put_league_state("pending_claims", {"x": 1}, ref=ref)
    db.put_my_cap("QB", 3, ref=ref)
    db.put_subscription(f"https://push.test/{ref.user_id}", {"p256dh": "k", "auth": "a"}, "ua", user_id=ref.user_id)
    db.put_watch(101, True, ref=ref)
    db.add_watch_signals([{"key": f"s{ref.user_id}", "player_id": 101, "kind": "usage", "text": "t",
                           "at": notify.now()}], ref=ref)
    db.put_seats({5: 6}, ref=ref)
    db.put_claim_plan({"week": 3, "groups": []}, ref=ref)
    db.add_notices([{"key": f"k{ref.user_id}", "kind": "moves", "title": "t", "body": "b", "url": "/",
                     "created_at": notify.now()}], ref=ref)
    db.add_decision({"at": "2026-10-27T12:00:00-04:00", "week": 3, "kind": "note", "source": "app", "title": "t",
                     "decision": "d"}, ref=ref)
    db.put_sit(3, 202, True, ref=ref)
    db.create_session(ref.user_id, "ua")
    with db._write() as c:
        c.execute(sa.insert(db.page_cache).values(league=ref.key, season=ref.season, key="week:3", payload={},
                                                  built_at=0, user_id=ref.user_id))


def test_delete_leaves_no_personal_row_and_shared_team_rows_stay(monkeypatch):
    two = two_users.make()
    _fill(two.a)
    _fill(two.b)
    db.sign_in_user("google", "g-a", "a@example.com", "A")  # an identity for someone else's email: untouched
    before_b = _owned(two.b_id)
    assert _owned(two.a_id)
    c = _signed_in(two.a_id)
    assert c.post("/auth/delete", json={"email": "nope@example.com"}).status_code == 422
    assert _owned(two.a_id)
    email = db.user_row(two.a_id)["email"]
    r = c.post("/auth/delete", json={"email": email.upper()})
    assert r.status_code == 200
    assert _owned(two.a_id) == {}
    assert _owned(two.b_id) == before_b
    assert _team_rows(HOME_KEY, 7)  # C still co-manages team 7: its plan, seats, pending claims stay
    assert leagues.membership(two.c.user_id, HOME_KEY).team_id == 7
    assert c.get("/account").status_code == 401


def test_delete_takes_a_teams_rows_when_nobody_else_is_on_it():
    two = two_users.make()
    _fill(two.b)
    _fill(two.b_only)
    c = _signed_in(two.b_id)
    assert c.post("/auth/delete", json={"email": "b@example.com"}).status_code == 200
    assert _owned(two.b_id) == {}
    assert _team_rows(HOME_KEY, 3) == 0 and _team_rows("espn:999", 4) == 0
    assert leagues.members("espn:999") == []  # the league stays, memberless
    assert any(r.key == "espn:999" for r in leagues.all_leagues())


def test_delete_drops_the_users_in_memory_caches():
    two = two_users.make()
    api.cache_for(two.b)
    api.cache_for(two.a)
    _signed_in(two.b_id).post("/auth/delete", json={"email": "b@example.com"})
    assert (two.b_id, HOME_KEY) not in api.caches and (two.a_id, HOME_KEY) in api.caches


def test_the_jobs_skip_a_league_nobody_is_in(monkeypatch):
    two = two_users.make()
    db.delete_user(two.b_id)
    assert "espn:999" in [r.key for r in leagues.all_leagues()]
    assert [r.key for r in leagues.joined()] == [HOME_KEY]
    ran = []
    monkeypatch.setattr(notify, "league_tick", lambda ref: ran.append(ref.key) or {})
    monkeypatch.setattr(notify, "flush", lambda *a, **k: {})
    monkeypatch.setattr(notify, "check_sources", lambda *a, **k: [])
    notify.tick()
    assert ran == [HOME_KEY]


# --- linking a second sign-in ---

def _google(monkeypatch, info: dict):
    g = auth._google(CID, CSECRET)

    async def redirect(request, redirect_uri=None, **kw):
        return RedirectResponse("https://accounts.google.test/o/oauth2/auth", status_code=302)

    async def token(request, **kw):
        return {"userinfo": info}
    monkeypatch.setattr(g, "authorize_redirect", redirect)
    monkeypatch.setattr(g, "authorize_access_token", token)


def _link(c: TestClient):
    r = c.get("/auth/google/link")
    assert r.status_code == 302 and r.headers["location"].startswith("https://accounts.google.test/")
    return c.get("/auth/google/callback", params={"code": "c", "state": "s"})


def test_link_adds_an_identity_to_the_signed_in_user(monkeypatch):
    uid = db.create_user("me@example.com", "Me")
    _google(monkeypatch, {"sub": "g-2", "email": "other@gmail.com", "email_verified": True})
    c = _signed_in(uid)
    r = _link(c)
    assert r.status_code == 302 and r.headers["location"] == "/settings?link=linked"
    assert db.identity_user("google", "g-2") == uid  # not on FFL_SIGNUP_EMAILS: linking needs no sign-up
    assert [i["email"] for i in c.get("/account").json()["identities"]] == ["other@gmail.com"]
    assert c.get("/account").json()["linkable"] == []


def test_link_never_moves_another_users_identity(monkeypatch):
    theirs = db.sign_in_user("google", "g-9", "them@example.com", None)
    _google(monkeypatch, {"sub": "g-9", "email": "them@example.com", "email_verified": True})
    c = _signed_in(db.create_user("me@example.com", "Me"))
    assert _link(c).headers["location"] == "/settings?link=taken"
    assert db.identity_user("google", "g-9") == theirs


def test_a_sign_in_after_an_abandoned_link_is_not_a_link(monkeypatch):
    """Link started, never finished at Google, then a plain sign-in: it signs in, it links nothing."""
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "new@example.com")
    _google(monkeypatch, {"sub": "g-5", "email": "new@example.com", "email_verified": True, "name": "N"})
    me = db.create_user("me@example.com", "Me")
    c = _signed_in(me)
    assert c.get("/auth/google/link").status_code == 302
    c.get("/auth/google/start")
    r = c.get("/auth/google/callback", params={"code": "c", "state": "s"})
    assert r.headers["location"] == "/"
    assert db.identity_user("google", "g-5") not in (None, me)
