"""Which release an install runs, and whether a game is on (what holds a scheduled upgrade)."""
import pytest
import requests
from fastapi.testclient import TestClient

from ffl import api, auth, db, live
from tests.conftest import TEST_USER

RUNNING, NEWER = "a" * 40, "b" * 40


class Reply:
    def __init__(self, sha):
        self.sha = sha

    def raise_for_status(self):
        pass

    def json(self):
        return {"ref": "refs/tags/release", "object": {"sha": self.sha, "type": "commit"}}


@pytest.fixture
def c(monkeypatch):
    db.set_admin(TEST_USER["id"], True)
    monkeypatch.setattr(api, "_RELEASE", {})
    monkeypatch.setenv("FFL_COMMIT", RUNNING)
    monkeypatch.delenv("FFL_VERSION", raising=False)
    return TestClient(api.app, raise_server_exceptions=False)


def released(monkeypatch, sha):
    calls = []
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: calls.append(a) or Reply(sha))
    return calls


def test_up_to_date(c, monkeypatch):
    released(monkeypatch, RUNNING)
    assert c.get("/app/version").json() == {"commit": RUNNING, "latest": RUNNING, "behind": False, "pinned": None}


def test_behind(c, monkeypatch):
    released(monkeypatch, NEWER)
    assert c.get("/app/version").json() == {"commit": RUNNING, "latest": NEWER, "behind": True, "pinned": None}


def test_github_is_asked_once_in_a_while(c, monkeypatch):
    calls = released(monkeypatch, NEWER)
    c.get("/app/version")
    c.get("/app/version")
    assert len(calls) == 1


def test_github_down_is_unknown_not_an_error_and_is_asked_again(c, monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("no route")
    monkeypatch.setattr(api.requests, "get", down)
    r = c.get("/app/version")
    assert r.status_code == 200 and r.json() == {"commit": RUNNING, "latest": None, "behind": False, "pinned": None}
    released(monkeypatch, NEWER)
    assert c.get("/app/version").json()["behind"] is True


def test_a_dev_build_is_never_behind(c, monkeypatch):
    monkeypatch.delenv("FFL_COMMIT")
    released(monkeypatch, NEWER)
    assert c.get("/app/version").json() == {"commit": None, "latest": NEWER, "behind": False, "pinned": None}


def test_a_pinned_install_is_never_behind_and_never_asks(c, monkeypatch):
    monkeypatch.setenv("FFL_VERSION", "v1.2.0")
    calls = released(monkeypatch, NEWER)
    assert c.get("/app/version").json() == {"commit": RUNNING, "latest": None, "behind": False, "pinned": "v1.2.0"}
    assert calls == []


def test_version_is_admins(c):
    db.set_admin(TEST_USER["id"], False)
    assert c.get("/app/version").status_code == 403


@pytest.mark.parametrize("connected, status, hold", [
    (True, "in", True),      # a game is on
    (True, "post", False),   # every game over
    (True, "pre", False),    # none started
    (False, "in", False),    # the feed dropped: a stale "in" must not hold an upgrade forever
])
def test_hold_while_a_game_is_on(c, monkeypatch, connected, status, hold):
    monkeypatch.setattr(live.STATE, "connected", connected)
    monkeypatch.setattr(live.STATE, "games", {"KC": {"status": status}})
    assert c.get("/upgrade/hold").json() == {"hold": hold}


def test_hold_with_no_feed_yet(c, monkeypatch):
    monkeypatch.setattr(live.STATE, "connected", False)
    monkeypatch.setattr(live.STATE, "games", {})
    assert c.get("/upgrade/hold").json() == {"hold": False}


@pytest.mark.real_auth
def test_hold_is_the_job_tokens_alone(monkeypatch):
    monkeypatch.setenv("FFL_JOB_TOKEN", "tok")
    assert auth.decide("GET", "/upgrade/hold", None, "tok")[0] == 0
    assert auth.decide("GET", "/upgrade/hold", None, "wrong")[0] == 401
    assert auth.decide("GET", "/upgrade/hold", None, None)[0] == 401
