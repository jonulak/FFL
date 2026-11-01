"""The news desk's classifier: typed questions about one state, typed answers back, through `claude -p` (Haiku).

Questions are typed (`{"type": "noul", "instructions"}` or `{"type": "choice", "instructions", "criteria"}`),
and so are the answers (`{"type": "noul", "noul"}`, `{"type": "choice", "choice", "probabilities", "confidence"}`),
the shapes `tag.py` reads. One call per state, every question in it; the state's text, a question's `story` facts and
`tag.py`'s story labels are fenced as untrusted source material.
"""
from __future__ import annotations

import json
import subprocess

from .. import claude_cli

# scores are the model's own 0..1 estimates, not calibrated probabilities; tag.py's thresholds were set on a calibrated
# classifier's, so a threshold may need moving (the bench is the check)
MODEL = "haiku"
TIMEOUT_S = 120
CWD = claude_cli.neutral_cwd("news-tag")
# The account, not the request: no login or token, out of quota or credit. Every item would fail alike.
ACCOUNT_MARKS = ("not logged in", "no claude token", "quota", "rate limit", "usage limit", "credit")  # lowercase
UNIT = {"type": "number", "minimum": 0, "maximum": 1}


class ClassifyError(RuntimeError):
    """A call that gave no answers. `status` is None when no answer came at all (the CLI failing); 422 when the
    answer was unusable (this item's); `account` is the login or its quota, which every item would hit alike."""

    def __init__(self, message: str, status: int | None = None, account: bool = False):
        super().__init__(message)
        self.status = status
        self.account = account


class Answers(dict):
    """The answers, keyed like the questions; `usage` is the call's own ({input_tokens, output_tokens}, {} if none)."""
    usage: dict = {}


def schema_for(questions: dict) -> dict:
    props = {k: UNIT if q["type"] == "noul" else
             {"type": "object", "properties": {"choice": {"type": "string", "enum": list(q["criteria"])},
                                               "probability": UNIT},
              "required": ["choice", "probability"]}
             for k, q in questions.items()}
    return {"type": "object", "properties": props, "required": list(questions)}


def _line(key: str, q: dict) -> str:
    ins = q["instructions"]
    if isinstance(ins, dict):  # tag.weigh's: the question, and the story's facts (other sources' text) fenced
        line = f"{key}: {ins['question']}\n" + claude_cli.data_block("source", json.dumps(ins["story"]), role="story")
    else:
        line = f"{key}: {ins}"
    if q["type"] == "choice":
        line += "\n" + "\n".join(f"  - {c}: {d}" for c, d in q["criteria"].items())
    return line


def prompt(state, questions: dict) -> str:
    if isinstance(state, dict):
        src = claude_cli.data_block("source", state.get("text"), **{k: v for k, v in state.items() if k != "text"})
    else:
        src = claude_cli.data_block("source", str(state))
    return ("You classify fantasy football news. Read the text below and answer every question about it.\n"
            f"{claude_cli.DATA_RULE}\n\n{src}\n\n"
            "Each question is a key, then what it asks. A statement question's answer is a number from 0 to 1: how "
            "likely the statement is true of the text. A question with options listed under it answers with "
            "`choice`, one of those option keys exactly, and `probability`, 0 to 1, how likely that choice is "
            "right.\n\n" + "\n".join(_line(k, q) for k, q in questions.items()))


def _unit(x) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ClassifyError(f"classify: not a number: {x!r}", 422)
    return min(1.0, max(0.0, float(x)))


def parse(out: dict, questions: dict) -> Answers:
    """The answer shapes out of the schema'd object; a missing answer or a choice outside the criteria raises
    ClassifyError(status=422)."""
    missing = set(questions) - set(out)
    if missing:
        raise ClassifyError(f"classify: no answer to {sorted(missing)[:5]}", 422)
    got = Answers()
    for k, q in questions.items():
        a = out[k]
        if q["type"] == "noul":
            got[k] = {"type": "noul", "noul": _unit(a)}
            continue
        choice = a.get("choice") if isinstance(a, dict) else None
        if choice not in q["criteria"]:
            raise ClassifyError(f"classify: {k} answered {choice!r}, not one of {list(q['criteria'])}", 422)
        p = _unit(a.get("probability"))
        got[k] = {"type": "choice", "choice": choice, "probabilities": {choice: p}, "confidence": p}
    return got


def ask(state, questions: dict, run_cli=claude_cli.run) -> Answers:
    """The answers, keyed like `questions`, with the call's `usage`. Raises ClassifyError on a failed call or an
    unusable answer."""
    try:
        out = run_cli(prompt(state, questions), model=MODEL, schema=schema_for(questions), tools="", cwd=CWD,
                      timeout=TIMEOUT_S, kind="news_tag")
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:  # a timeout or bad JSON too
        raise ClassifyError(f"classify: {e}", account=any(m in str(e).lower() for m in ACCOUNT_MARKS)) from e
    got = parse(out, questions)
    u = getattr(out, "usage", None) or {}
    got.usage = {"input_tokens": u.get("input", 0), "output_tokens": u.get("output", 0)}
    return got
