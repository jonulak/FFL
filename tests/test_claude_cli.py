import json
import subprocess

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
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)  # kept on purpose; this test is about the rest
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    seen = {}

    def fake_run(args, **kw):
        seen.update(args=args, **kw)
        return subprocess.CompletedProcess(args, 0, json.dumps({"structured_output": {"ok": 1}}), "")

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    cwd = tmp_path / "neutral"
    out = claude_cli.run("hi", model="haiku", schema={"type": "object"}, tools="", cwd=cwd, timeout=9)
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
    """The box logs the CLI in with CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`); the nested-session markers go,
    the token stays."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: "/bin/claude")
    seen = {}

    def fake_run(args, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(args, 0, json.dumps({"structured_output": {}}), "")

    monkeypatch.setattr(claude_cli.subprocess, "run", fake_run)
    claude_cli.run("hi", model="haiku", schema={"type": "object"}, tools="", cwd=tmp_path, timeout=9)
    assert [k for k in seen["env"] if k.startswith("CLAUDE")] == ["CLAUDE_CODE_OAUTH_TOKEN"]
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
