"""Settings › Claude: an admin stores, tests, reports and deletes the Claude token; the value never comes back."""
import pytest
from fastapi.testclient import TestClient

from ffl import api, claude_cli, db
from tests.conftest import TEST_USER

ME = TEST_USER["id"]


@pytest.fixture
def c(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    db.set_admin(ME, True)
    return TestClient(api.app, raise_server_exceptions=False)


def _stub(monkeypatch, answer):
    seen = []

    def run(prompt, **kw):
        seen.append(kw)
        if isinstance(answer, Exception):
            raise answer
        return claude_cli.Structured(answer)
    monkeypatch.setattr(claude_cli, "run", run)
    return seen


def test_store_test_report_delete(c, monkeypatch):
    assert c.get("/llm/token").json() == {"set": False, "source": None}
    seen = _stub(monkeypatch, {"ok": "ok"})
    r = c.post("/llm/token", json={"token": " sk-ant-oat01-x \n"})
    assert r.status_code == 200 and r.json() == {"set": True}
    assert seen[0]["token"] == "sk-ant-oat01-x" and seen[0]["kind"] == "ping" and seen[0]["tools"] == ""
    assert db.get_secret(claude_cli.TOKEN_KEY) == "sk-ant-oat01-x"
    body = c.get("/llm/token").json()
    assert body == {"set": True, "source": "db"} and "sk-ant" not in str(body)
    assert c.post("/llm/token/delete").json() == {"set": False}
    assert db.get_secret(claude_cli.TOKEN_KEY) is None
    assert c.get("/llm/token").json() == {"set": False, "source": None}, "the cache forgot the deleted token"


def test_a_failed_ping_stores_nothing(c, monkeypatch):
    _stub(monkeypatch, RuntimeError("Not logged in"))
    r = c.post("/llm/token", json={"token": "sk-ant-oat01-bad"})
    assert r.status_code == 502 and r.json() == {"error": "claude_failed", "message": "Not logged in"}
    assert db.get_secret(claude_cli.TOKEN_KEY) is None


def test_a_ping_timeout_or_echo_is_a_502_without_the_token(c, monkeypatch):
    import subprocess
    _stub(monkeypatch, subprocess.TimeoutExpired(["claude"], 60))
    r = c.post("/llm/token", json={"token": "sk-ant-oat01-slow"})
    assert r.status_code == 502 and r.json()["error"] == "claude_failed" and "timed out" in r.json()["message"]
    _stub(monkeypatch, RuntimeError("bad key sk-ant-oat01-echo"))
    r = c.post("/llm/token", json={"token": "sk-ant-oat01-echo"})
    assert r.status_code == 502 and r.json()["message"] == "bad key …"
    assert db.get_secret(claude_cli.TOKEN_KEY) is None


def test_a_blank_token_is_refused_without_a_ping(c, monkeypatch):
    seen = _stub(monkeypatch, {"ok": "ok"})
    r = c.post("/llm/token", json={"token": "  \n"})
    assert r.status_code == 422 and r.json() == {"error": "no_token"} and not seen


def test_the_environments_token(c, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-env")
    assert c.get("/llm/token").json() == {"set": True, "source": "env"}


def test_a_non_admin_is_refused(c):
    db.set_admin(ME, False)
    for method, path, body in (("GET", "/llm/token", None), ("POST", "/llm/token", {"token": "x"}),
                               ("POST", "/llm/token/delete", None)):
        r = c.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "not_admin", path
