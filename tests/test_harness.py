"""The agent runner (`ffl/harness.py`): its contract, its HTTP server, and the API's client of it (`claude_cli.run`
with FFL_RUNNER_URL set)."""
import ast
import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa

from ffl import claude_cli, db, harness

REQ = {"harness": "claude", "prompt": "hi", "schema": {"type": "object"}, "model": "haiku", "tools": "",
       "timeout": 9, "credential": "sk-ant-oat01-secret", "cwd": "test"}


def _cli(monkeypatch, *answers):
    monkeypatch.setattr(harness.shutil, "which", lambda name: "/bin/claude")
    seen = []

    def fake(args, **kw):
        seen.append((args, kw))
        a = answers[len(seen) - 1]
        if isinstance(a, BaseException):
            raise a
        return a
    monkeypatch.setattr(harness.subprocess, "run", fake)
    return seen


def test_harness_imports_only_the_standard_library():
    """The runner image copies ffl/harness.py alone, with no `uv sync`: one third-party import would break it."""
    tree = ast.parse(Path(harness.__file__).read_text())
    mods = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
    mods |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(isinstance(n, ast.ImportFrom) and n.level for n in ast.walk(tree)), "no relative imports"
    assert mods <= set(sys.stdlib_module_names) | {"__future__"}, mods - set(sys.stdlib_module_names)


def test_call_answers_with_the_output_and_usage(monkeypatch):
    env = {"structured_output": {"ok": 1}, "total_cost_usd": 0.01, "usage": {"input_tokens": 3, "output_tokens": 2}}
    seen = _cli(monkeypatch, subprocess.CompletedProcess([], 0, json.dumps(env), ""))
    assert harness.call(REQ) == {"ok": True, "output": {"ok": 1}, "usage": {"input": 3, "output": 2, "cost_usd": 0.01}}
    args, kw = seen[0]
    assert kw["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-secret"
    assert kw["cwd"] == str(harness.neutral_cwd("test"))


def test_call_marks_an_account_error_and_scrubs_the_credential(monkeypatch):
    out = json.dumps({"is_error": True, "result": "Not logged in (sk-ant-oat01-secret)", "total_cost_usd": 0.002})
    _cli(monkeypatch, subprocess.CompletedProcess([], 1, out, ""))
    got = harness.call(REQ)
    assert got["ok"] is False and got["account"] is True and got["timeout"] is False
    assert "sk-ant-oat01-secret" not in got["error"] and "Not logged in" in got["error"]
    assert got["usage"]["cost_usd"] == 0.002


def test_call_says_a_call_error_timeout_unknown_harness_and_no_credential(monkeypatch):
    _cli(monkeypatch, subprocess.CompletedProcess([], 0, "garbage", ""), subprocess.TimeoutExpired("claude", 9))
    bad = harness.call(REQ)
    assert bad["ok"] is False and bad["account"] is False and bad["timeout"] is False
    t = harness.call(REQ)
    assert t["ok"] is False and t["timeout"] is True
    assert "unknown harness" in harness.call({**REQ, "harness": "nope"})["error"]
    none = harness.call({**REQ, "credential": ""})
    assert none["account"] is True and "no Claude token" in none["error"]
    assert "KeyError" in harness.call({"credential": "x"})["error"]  # a malformed request is an answer, not a crash


@pytest.fixture
def runner():
    srv = harness.serve("job-secret", port=0, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_server_needs_the_shared_secret_and_round_trips(runner, monkeypatch):
    _cli(monkeypatch, subprocess.CompletedProcess([], 0, json.dumps({"structured_output": {"a": 1}}), ""))
    assert httpx.post(runner + "/run", json=REQ).status_code == 403
    assert httpx.post(runner + "/run", json=REQ, headers={"x-ffl-job": "wrong"}).status_code == 403
    r = httpx.post(runner + "/run", json=REQ, headers={"x-ffl-job": "job-secret"})
    assert r.status_code == 200 and r.json()["output"] == {"a": 1}
    assert httpx.post(runner + "/run", content=b"[", headers={"x-ffl-job": "job-secret"}).status_code == 400
    assert httpx.get(runner + "/health").json() == {"ok": True}


def test_server_will_not_start_without_a_secret():
    with pytest.raises(SystemExit):
        harness.serve("", port=0)


# ---- the API's side: claude_cli.run against a runner ----

@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-env")
    monkeypatch.setenv("FFL_JOB_TOKEN", "job-secret")
    claude_cli.forget_token()
    monkeypatch.setattr(harness.subprocess, "run", lambda *a, **k: pytest.fail("the API ran a subprocess"))
    monkeypatch.setattr(harness.shutil, "which", lambda name: None)  # the API image has no CLI


def _stub(reply: bytes, status: int = 200):
    """A runner that answers every POST with `reply`."""
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(status)
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run(**kw):
    return claude_cli.run("hi", model="haiku", schema={"type": "object"}, tools="", cwd=Path("/x/news"), timeout=9,
                          kind="news", **kw)


def _ledger():
    with db.engine().connect() as c:
        return c.execute(sa.text("SELECT kind, model, ok, cost_usd FROM llm_calls ORDER BY id")).all()


def test_client_answers_through_the_runner(api_env, monkeypatch):
    srv = _stub(json.dumps({"ok": True, "output": {"a": 1}, "usage": {"input": 4, "output": 1, "cost_usd": 0.5}}).encode())
    monkeypatch.setenv("FFL_RUNNER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    got = _run()
    srv.shutdown()
    assert got == {"a": 1} and got.usage == {"input": 4, "output": 1, "cost_usd": 0.5}
    assert _ledger() == [("news", "haiku", True, 0.5)]


@pytest.mark.parametrize("reply,status,account,match", [
    (json.dumps({"ok": False, "error": "claude: Not logged in", "account": True, "usage": {"cost_usd": 0.1}}), 200,
     True, "Not logged in"),
    (json.dumps({"ok": False, "error": "claude exited 1: boom", "account": False}), 200, False, "boom"),
    ("not json", 200, True, "not JSON"),
    ("{}", 403, True, "403"),
    (json.dumps({"ok": True, "output": [1]}), 200, False, "not a JSON object"),
])
def test_client_errors_are_typed_and_still_ledgered(api_env, monkeypatch, reply, status, account, match):
    srv = _stub(reply.encode(), status)
    monkeypatch.setenv("FFL_RUNNER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    with pytest.raises(claude_cli.LlmError, match=match) as e:
        _run()
    srv.shutdown()
    assert e.value.account is account
    assert [r[2] for r in _ledger()] == [False]


def test_client_turns_a_runner_timeout_into_timeout_expired(api_env, monkeypatch):
    """outlook.generate stops a batch's retries on subprocess.TimeoutExpired: the runner's timeout must stay one."""
    srv = _stub(json.dumps({"ok": False, "error": "timed out", "timeout": True, "account": False}).encode())
    monkeypatch.setenv("FFL_RUNNER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    with pytest.raises(subprocess.TimeoutExpired):
        _run()
    srv.shutdown()


def test_client_with_the_runner_down_is_an_account_error(api_env, monkeypatch):
    with socket.socket() as s:  # a port nobody listens on
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("FFL_RUNNER_URL", f"http://127.0.0.1:{port}")
    with pytest.raises(claude_cli.LlmError, match="runner unreachable") as e:
        _run()
    assert e.value.account is True and _ledger() == [("news", "haiku", False, 0.0)]


def test_the_settings_ping_works_through_the_runner_with_no_cli_in_the_api(api_env, runner, monkeypatch):
    """The API image has no `claude`: the ping (Settings › Claude) still answers, over the runner."""
    from ffl import api
    monkeypatch.setenv("FFL_RUNNER_URL", runner)
    calls = []

    def fake(req):
        calls.append(req)
        return {"ok": True, "output": {"ok": "ok"}, "usage": {"input": 1, "output": 1, "cost_usd": 0.0}}
    monkeypatch.setattr(harness, "call", fake)  # the runner thread shares this module
    api.ping("sk-ant-api03-given")
    assert calls[0]["credential"] == "sk-ant-api03-given" and calls[0]["model"] == "haiku"
