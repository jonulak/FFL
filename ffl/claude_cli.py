"""The headless Claude Code CLI (`claude -p`), shared by the outlook writer and the news writer.

The caller owns the model, the schema, the tool list and the working directory; this owns what is the
same for every call: the flags, the environment, the envelope and its errors.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HOOKS_OFF = json.dumps({"disableAllHooks": True})


def parse_envelope(stdout: str) -> dict:
    """The schema'd object out of `claude -p --output-format json`.

    The envelope is one JSON object: `is_error` + `result` on failure ("Not logged in …"),
    `structured_output` holding the object on success; older CLIs put that JSON in `result` instead.
    """
    d = json.loads(stdout)
    if d.get("is_error"):
        raise RuntimeError(f"claude: {d.get('result')}")
    out = d.get("structured_output")
    if not isinstance(out, dict):
        out = json.loads(d.get("result") or "{}")
    return out


def run(prompt: str, *, model: str, schema: dict, tools: str, cwd: Path, timeout: int) -> dict:
    """One call through the CLI: `model`, structured output to `schema`, the prompt on stdin.

    `tools` is a comma list, allowed up front (there is nobody to answer a permission prompt); "" gives the
    model no tools at all (`--tools ""` disables every built-in, per `claude --help`).
    """
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not on PATH")
    # A Claude Code session marks its children with CLAUDE* variables; drop them so the CLI does not
    # treat this as a nested session. Harmless under launchd, where they never exist.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    # No --bare: it skips the keychain, and every call then answers "Not logged in". The CLI reads
    # CLAUDE.md from its working directory, so run it from a neutral one, not the repo — the prompt
    # is the whole input. Hooks off: the user's global ones (moshi's phone notifications, plugin
    # session hooks) would otherwise fire on every run.
    cwd.mkdir(parents=True, exist_ok=True)
    args = [exe, "-p", "--model", model, "--tools", tools]
    if tools:
        args += ["--allowedTools", tools]
    args += ["--no-session-persistence", "--settings", HOOKS_OFF, "--output-format", "json",
             "--json-schema", json.dumps(schema)]
    r = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, env=env,
                       cwd=str(cwd))
    if r.returncode != 0:
        # the CLI exits 1 with its reason in the JSON envelope ("Not logged in …"), not on stderr
        try:
            parse_envelope(r.stdout)
        except ValueError:
            pass
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr.strip()[:200]}")
    return parse_envelope(r.stdout)
