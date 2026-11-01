"""LLM calls for the outlook writer and the news desk: the API's side of the agent runner (`ffl/harness.py`).

The caller owns the model, the schema, the tool list and the prompt; this resolves the credential, sends the call to
the runner (FFL_RUNNER_URL, the compose stack's `runner` service) or, with no runner URL, to `harness.call` in process
(dev on the Mac, tests), turns the reply back into a `Structured` or an error, and writes the `llm_calls` row.
"""
from __future__ import annotations

import os
import shutil  # noqa: F401  (tests patch claude_cli.shutil / claude_cli.subprocess: the same modules harness uses)
import subprocess
import time
from pathlib import Path

import requests

from . import db, harness, vault
from .harness import DATA_RULE, HOOKS_OFF, data_block, neutral_cwd  # noqa: F401  (imported from here by callers)


class Structured(dict):
    """The schema'd object; `usage` is the call's {input, output, cost_usd}, input counting the cache's tokens too."""
    usage: dict = {"input": 0, "output": 0, "cost_usd": 0.0}


class LlmError(RuntimeError):
    """A failed call. `account`: the login, the plan or the runner failed, so every other call would fail too."""

    def __init__(self, message: str, *, account: bool = False):
        super().__init__(message)
        self.account = account


def parse_envelope(stdout: str) -> Structured:
    """The schema'd object out of `claude -p --output-format json`, with the envelope's usage (harness.parse_envelope)."""
    out, spent = harness.parse_envelope(stdout)
    out = Structured(out)
    out.usage = spent
    return out


TOKEN_KEY = "claude_token"
TOKEN_TTL_S = 60
_TOKEN: tuple[str | None, float] | None = None   # (the sealed row's value, when it was read)


def forget_token() -> None:
    global _TOKEN
    _TOKEN = None


def _stored_token() -> str | None:
    global _TOKEN
    t = _TOKEN  # read once: forget_token() on a request thread may clear the global mid-read
    if t is None or time.monotonic() - t[1] > TOKEN_TTL_S:
        try:
            value = db.get_secret(TOKEN_KEY)
        except vault.VaultError as e:  # sealed under a ring this host lacks: as if none were stored
            print(f"claude_cli: the stored Claude token does not open: {e}")
            value = None
        _TOKEN = t = (value, time.monotonic())  # cached either way, so a bad row is said once a minute at most
    return t[0]


def token_env(token: str | None = None) -> dict:
    """The CLI's login: `token`, else the sealed `claude_token` row (Settings › Claude), else the environment's
    CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY; under the variable its kind needs (`claude setup-token`'s
    sk-ant-oat… is an OAuth token, anything else an API key)."""
    t = token or _stored_token() or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not t:
        raise LlmError("no Claude token: Settings › Claude", account=True)
    return harness.credential_env(t)


def run(prompt: str, *, model: str, schema: dict, tools: str, cwd: Path, timeout: int, kind: str,
        league: str | None = None, player_id: int | None = None, token: str | None = None) -> Structured:
    """One call through the harness: `model`, structured output to `schema`. Every call, answered or failed, is a row
    in `llm_calls` (`kind`: outlook / news / news_tag / ping; `league`, `player_id` when known).

    `tools` is a comma list, allowed up front; "" gives the model no tools at all. `cwd` names the neutral working
    directory the harness runs in (its last part; `neutral_cwd`). `token` (a test of one not yet stored) wins over
    the stored one and the environment's. Raises LlmError (a RuntimeError; `.account` for a login, plan or runner
    failure) or subprocess.TimeoutExpired.
    """
    bill = {"input": 0, "output": 0, "cost_usd": 0.0, "ok": False}
    try:
        cred = next(iter(token_env(token).values()))
        req = {"harness": "claude", "prompt": prompt, "schema": schema, "model": model, "tools": tools,
               "timeout": timeout, "credential": cred, "cwd": Path(cwd).name}
        url = os.environ.get("FFL_RUNNER_URL")
        reply = _post(url, req, timeout) if url else harness.call(req)
        if not isinstance(reply, dict):
            raise LlmError("runner: a reply that is not a JSON object", account=True)
        bill.update({k: v for k, v in (reply.get("usage") or {}).items() if k in ("input", "output", "cost_usd")})
        if reply.get("timeout"):
            raise subprocess.TimeoutExpired("claude", timeout)
        if not reply.get("ok"):
            raise LlmError(str(reply.get("error") or "runner: a failed call with no reason"),
                           account=bool(reply.get("account")))
        out = reply.get("output")
        if not isinstance(out, dict):
            raise LlmError("runner: an answer that is not a JSON object")
        out = Structured(out)
        out.usage = {k: bill[k] for k in ("input", "output", "cost_usd")}
        bill["ok"] = True
        return out
    finally:
        _ledger(kind=kind, model=model, league=league, player_id=player_id, **bill)


def _post(url: str, req: dict, timeout: int):
    """The call on the runner. Down, refusing the secret or answering garbage is the runner's failure: account-wide,
    since every other call would meet it too."""
    try:
        r = requests.post(url.rstrip("/") + "/run", json=req, timeout=timeout + 15,
                       headers={"x-ffl-job": os.environ.get("FFL_JOB_TOKEN", "")})
    except requests.RequestException as e:
        raise LlmError(f"runner unreachable at {url}: {type(e).__name__}", account=True) from e
    if r.status_code != 200:
        raise LlmError(f"runner answered {r.status_code}", account=True)
    try:
        return r.json()
    except ValueError as e:
        raise LlmError("runner: a reply that is not JSON", account=True) from e


def _ledger(**row) -> None:
    """The call's llm_calls row; a ledger that cannot be written never fails the call it records."""
    try:
        db.add_llm_call(**row)
    except Exception as e:  # noqa: BLE001 — any DB trouble, including no schema, costs only the row
        print(f"claude_cli: llm_calls row not written: {type(e).__name__}: {e}")
