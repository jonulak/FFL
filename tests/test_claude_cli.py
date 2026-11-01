import json
import subprocess
import time

import pytest

from ffl import claude_cli


def test_neutral_cwd_is_outside_the_repo_and_data_with_no_claude_md_above():
    from ffl import config, outlook
    from ffl.newsdesk import write
    for d in (claude_cli.neutral_cwd("x"), outlook.OUTLOOK_DIR, write.CLAUDE_CWD):
        assert not d.is_relative_to(config.ROOT) and not d.is_relative_to(config.DATA)
        assert not any((p / "CLAUDE.md").exists() for p in (d, *d.parents))


def test_parse_envelope_error_raises_reason():
    with pytest.raises(RuntimeError, match="Not logged in"):
        claude_cli.parse_envelope(json.dumps({"is_error": True, "result": "Not logged in"}))


def test_parse_envelope_structured_then_result():
    obj = {"a": 1}
    assert claude_cli.parse_envelope(json.dumps({"structured_output": obj, "result": "{}"})) == obj
    assert claude_cli.parse_envelope(json.dumps({"result": json.dumps(obj)})) == obj


def test_parse_envelope_carries_usage():
    """Every token read counts as input, cache writes and reads too (the envelope of a real call, 2026-10-19)."""
    env = {"structured_output": {"a": 1}, "total_cost_usd": 0.00097,
           "usage": {"input_tokens": 2, "cache_creation_input_tokens": 4486, "cache_read_input_tokens": 1067,
                     "output_tokens": 129}}
    got = claude_cli.parse_envelope(json.dumps(env))
    assert got == {"a": 1} and got.usage == {"input": 5555, "output": 129, "cost_usd": 0.00097}
    assert claude_cli.parse_envelope(json.dumps({"result": "{}"})).usage == {"input": 0, "output": 0, "cost_usd": 0.0}


def test_run_passes_model_and_strips_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)  # the token's own tests are below
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    seen = {}

    def fake_run(args, **kw):
        seen.update(args=args, **kw)
        return subprocess.CompletedProcess(args, 0, json.dumps({"structured_output": {"ok": 1}}), "")

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    cwd = tmp_path / "neutral"
    out = claude_cli.run("hi", model="haiku", schema={"type": "object"}, tools="", cwd=cwd, timeout=9, kind="news")
    assert out == {"ok": 1}
    args = seen["args"]
    assert args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == ""
    assert "--allowedTools" not in args
    assert "disableAllHooks" in args[args.index("--settings") + 1]
    assert not any(k.startswith("CLAUDE") for k in seen["env"])
    assert seen["cwd"] == str(cwd) and cwd.is_dir()
    assert seen["input"] == "hi" and seen["timeout"] == 9


def test_run_keeps_the_oauth_token(monkeypatch, tmp_path):
    """An environment's CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) still logs the CLI in with no stored one; the
    nested-session markers go, the token stays."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-tok")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(args, 0, json.dumps({"structured_output": {}}), "")

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    claude_cli.run("hi", model="haiku", schema={"type": "object"}, tools="", cwd=tmp_path, timeout=9, kind="news")
    assert [k for k in seen["env"] if k.startswith("CLAUDE")] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-tok" and "ANTHROPIC_API_KEY" not in seen["env"]


def test_run_writes_one_ledger_row_per_call_ok_or_not(monkeypatch, tmp_path):
    """Every call is an llm_calls row: an answer with its tokens and cost, a failure with what the envelope says it
    spent; a ledger write that fails costs the row, never the call."""
    import sqlalchemy as sa
    from ffl import db
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    env = {"structured_output": {"ok": 1}, "total_cost_usd": 0.02,
           "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 5}}
    answers = [subprocess.CompletedProcess([], 0, json.dumps(env), ""),
               subprocess.CompletedProcess([], 1, json.dumps({"is_error": True, "result": "Not logged in",
                                                              "total_cost_usd": 0.004}), "")]
    monkeypatch.setattr(claude_cli.subprocess, "run", lambda args, **kw: answers.pop(0))
    kw = dict(model="sonnet", schema={"type": "object"}, tools="", cwd=tmp_path, timeout=9)
    assert claude_cli.run("hi", kind="outlook", league="espn:1", player_id=7, **kw) == {"ok": 1}
    with pytest.raises(RuntimeError, match="Not logged in"):
        claude_cli.run("hi", kind="news", **kw)
    with db.engine().connect() as c:
        rows = [dict(r._mapping) for r in c.execute(sa.text(
            "SELECT kind, model, league, player_id, input, output, cost_usd, ok FROM llm_calls ORDER BY id"))]
    assert rows == [
        {"kind": "outlook", "model": "sonnet", "league": "espn:1", "player_id": 7, "input": 100, "output": 5,
         "cost_usd": 0.02, "ok": True},
        {"kind": "news", "model": "sonnet", "league": None, "player_id": None, "input": 0, "output": 0,
         "cost_usd": 0.004, "ok": False}]

    def broken(**row):
        raise db.DBError("down")
    monkeypatch.setattr(claude_cli.db, "add_llm_call", broken)
    answers.append(subprocess.CompletedProcess([], 0, json.dumps(env), ""))
    assert claude_cli.run("hi", kind="news", **kw) == {"ok": 1}


def test_secrets_are_sealed_in_app_secrets():
    import sqlalchemy as sa
    from ffl import db
    db.put_secret("claude_token", "abc")
    assert db.get_secret("claude_token") == "abc"
    with db.engine().connect() as c:
        raw = c.execute(sa.text("SELECT value FROM app_secrets WHERE key = 'claude_token'")).scalar_one()
    assert raw != "abc" and "abc" not in raw
    db.put_secret("claude_token", "def")
    assert db.get_secret("claude_token") == "def"
    db.del_secret("claude_token")
    assert db.get_secret("claude_token") is None


def _no_env_token(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude_cli.forget_token()


def test_token_env_prefers_the_sealed_row_over_the_environment(monkeypatch):
    from ffl import db
    _no_env_token(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-env")
    assert claude_cli.token_env() == {"ANTHROPIC_API_KEY": "sk-ant-api-env"}
    db.put_secret("claude_token", "sk-ant-oat01-row")
    claude_cli.forget_token()
    assert claude_cli.token_env() == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-row"}
    db.put_secret("claude_token", "sk-ant-api03-row")
    assert claude_cli.token_env() == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-row"}  # cached for 60 s
    claude_cli.forget_token()
    assert claude_cli.token_env() == {"ANTHROPIC_API_KEY": "sk-ant-api03-row"}


def test_stored_token_survives_a_forget_between_its_reads(monkeypatch):
    """An admin's save or removal (`forget_token` on the request thread) while a worker reads the cache: the worker
    gets the value it found, never a TypeError off the cleared global."""
    from ffl import db
    _no_env_token(monkeypatch)
    db.put_secret("claude_token", "sk-ant-oat01-row")
    assert claude_cli._stored_token() == "sk-ant-oat01-row"
    real = time.monotonic

    def racing():
        claude_cli.forget_token()  # lands between the cache check and the read of its timestamp
        return real()
    monkeypatch.setattr(claude_cli.time, "monotonic", racing)
    assert claude_cli._stored_token() == "sk-ant-oat01-row"


def test_token_env_without_any_token_raises(monkeypatch):
    _no_env_token(monkeypatch)
    with pytest.raises(RuntimeError, match="no Claude token: Settings › Claude"):
        claude_cli.token_env()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-env")
    assert claude_cli.token_env() == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-env"}


def test_token_env_treats_a_row_the_ring_cannot_open_as_none(monkeypatch):
    from ffl import db, vault
    _no_env_token(monkeypatch)
    db.put_secret("claude_token", "sk-ant-oat01-row")
    monkeypatch.setenv("FFL_SECRET_KEYS", vault.keygen())
    claude_cli.forget_token()
    with pytest.raises(RuntimeError, match="no Claude token"):
        claude_cli.token_env()


def test_run_feeds_the_token_and_drops_the_environments(monkeypatch, tmp_path):
    """The sealed row wins over the environment; a `token=` given to run wins over both (Settings' test call)."""
    from ffl import db
    _no_env_token(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-env")
    monkeypatch.setenv("CLAUDECODE", "1")
    db.put_secret("claude_token", "sk-ant-oat01-row")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    seen = []
    monkeypatch.setattr(claude_cli.subprocess, "run", lambda args, **kw: seen.append(kw["env"]) or
                        subprocess.CompletedProcess(args, 0, json.dumps({"structured_output": {}}), ""))
    kw = dict(model="haiku", schema={"type": "object"}, tools="", cwd=tmp_path, timeout=9, kind="news")
    claude_cli.run("hi", **kw)
    claude_cli.run("hi", token="sk-ant-api03-given", **kw)
    assert [{k: v for k, v in e.items() if k.startswith(("CLAUDE", "ANTHROPIC"))} for e in seen] == [
        {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-row"}, {"ANTHROPIC_API_KEY": "sk-ant-api03-given"}]


def test_run_without_a_token_raises_before_the_cli_and_still_writes_the_ledger_row(monkeypatch, tmp_path):
    import sqlalchemy as sa
    from ffl import db
    _no_env_token(monkeypatch)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(claude_cli.subprocess, "run", lambda *a, **k: pytest.fail("the CLI ran"))
    with pytest.raises(RuntimeError, match="no Claude token"):
        claude_cli.run("hi", model="haiku", schema={"type": "object"}, tools="", cwd=tmp_path, timeout=9, kind="news")
    with db.engine().connect() as c:
        assert c.execute(sa.text("SELECT kind, ok FROM llm_calls")).all() == [("news", False)]
