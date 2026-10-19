"""Jev, TypeSafe AI's fast classifier: one POST, typed questions about one state, typed answers back.

The key is `TYPESAFE_API_KEY` in `.env` (loaded by `ffl.config`). Calls are not chunked: Jev has no limit
on the number of questions, only on context (64k tokens a request, 32k for the state plus any one
question), and the spike (2026-10-19) put a 20-player injury report's 62 questions through one call in
0.22 s at 9.3k input tokens. `tag` caps the state's text instead. Billing is per input token, and the
state counts once a call however many questions it carries.
"""
from __future__ import annotations

import os

import requests

from .. import config  # noqa: F401 — loads .env, where the key lives

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT_S = 20


# The account, not the request: a bad or revoked key, no credits left, rate limited. Every item would fail alike.
ACCOUNT = frozenset({401, 402, 403, 429})


class JevError(RuntimeError):
    """A call that gave no answers. `status` is the HTTP status, None when no answer came at all. None or a
    5xx is the service failing; `account` (no key, or a status in ACCOUNT) is the key or its credits; any other
    status (a 4xx, or a 200 with answers missing) is this request's."""

    def __init__(self, message: str, status: int | None = None, account: bool = False):
        super().__init__(message)
        self.status = status
        self.account = account or status in ACCOUNT


def ask(state, questions: dict) -> dict:
    """The answers, keyed like `questions`. Raises JevError on no key, a failed call or a missing answer."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise JevError("no TYPESAFE_API_KEY in .env", account=True)
    try:
        r = requests.post(URL, headers={"Authorization": f"Bearer {key}"},
                          json={"state": state, "model": MODEL, "questions": questions}, timeout=TIMEOUT_S)
    except requests.RequestException as e:
        raise JevError(f"jev: {e}") from e
    if r.status_code != 200:
        raise JevError(f"jev {r.status_code}: {r.text[:300]}", r.status_code)
    try:
        answers = r.json()["answers"]
    except (ValueError, KeyError, TypeError) as e:
        raise JevError(f"jev: unreadable answer ({e})", r.status_code) from e
    missing = set(questions) - set(answers)
    if missing:
        raise JevError(f"jev: no answer to {sorted(missing)[:5]}", r.status_code)
    return answers
