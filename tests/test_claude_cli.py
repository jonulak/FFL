import json
import subprocess

import pytest

from ffl import claude_cli


def test_parse_envelope_error_raises_reason():
    with pytest.raises(RuntimeError, match="Not logged in"):
        claude_cli.parse_envelope(json.dumps({"is_error": True, "result": "Not logged in"}))


def test_parse_envelope_structured_then_result():
    obj = {"a": 1}
    assert claude_cli.parse_envelope(json.dumps({"structured_output": obj, "result": "{}"})) == obj
    assert claude_cli.parse_envelope(json.dumps({"result": json.dumps(obj)})) == obj


def test_run_passes_model_and_strips_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDECODE", "1")
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
