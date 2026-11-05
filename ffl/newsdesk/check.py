"""A second opinion on an arrow before it can push: Haiku reads the report behind it, not told the arrow, and says whether
it is about this player and which way it moves him. A different answer flags the row (`flag`), which never pushes.

Only rows that could push (notices.NEWS_PUSH_PROB) are asked about, a few a day. Measured on 80 hand-labelled arrows (issue
#120): 13 of 13 wrong ones caught and 2 of 67 right ones lost, against 6 and 6 for asking the writer to doubt an arrow it
is shown, and Sonnet did no better. Revisions wait in state `news_to_check` ([story, rev, at]) until asked: a failed call
holds their push for the next run, a late push being better than a wrong one, until notify.MAX_AGE lets them go.
"""
from __future__ import annotations

from datetime import datetime

from .. import claude_cli, db, notify
from . import usage
from .notices import NEWS_PUSH_PROB
from .players import Index

BATCH = 8
MODEL = "haiku"
TIMEOUT = 180
TEXT = 3000  # chars of the report
CWD = claude_cli.neutral_cwd("news-check")
QUEUE = "news_to_check"
SCHEMA = {"type": "object", "required": ["answers"], "properties": {"answers": {"type": "array", "items": {
    "type": "object", "required": ["n", "about_him", "effect"], "properties": {
        "n": {"type": "integer"}, "about_him": {"type": "boolean"},
        "effect": {"type": "string", "enum": ["up", "down", "none"]}}}}}}
INSTRUCTIONS = f"""You check fantasy football news reports, one player per report. For each:
`about_him`: is this report really about this exact player, not a teammate, someone with a similar name, or a passing mention?
`effect`: decide it yourself, from this report alone:
- up: this report says he is newly more available or has a bigger job than before (activated, returned to practice, cleared, named starter).
- down: this report says he is newly hurt, missing practice or games, or has lost his job.
- none: this report gives no new change for him: he is only mentioned, the status belongs to someone else with a similar name or to a teammate, it repeats old news, or it is a quote or a stat line.
{claude_cli.DATA_RULE}"""


def _rows(s: dict) -> list[dict]:
    """The arrows of a story that could push."""
    return [p for p in s["players"] if p["effect"] != "none" and p["prob"] >= NEWS_PUSH_PROB]


def report(s: dict, pid: int) -> dict:
    """The newest live source naming him, else the newest live one (every source, for a retracted story)."""
    items = [i for i in s["items"] if not i.get("withdrawn_at")] or s["items"]
    items = sorted(items, key=lambda i: (i["published_at"], i["id"]), reverse=True)
    return next((i for i in items if pid in (i.get("player_ids") or [])), items[0])


def prompt(batch: list[tuple[int, dict]], index: Index) -> str:
    """`batch`: (the answer's n, {name, pos, team, title, text})."""
    blocks = [f"## Report {n}: {r['name']}, {r['pos']}, {r['team'] or 'no club'}\n"
              + claude_cli.data_block("source", f"{r['title']}\n{(r['text'] or '')[:TEXT]}") for n, r in batch]
    return INSTRUCTIONS + "\n\n" + "\n\n".join(blocks)


def holds(answer: dict, effect: str) -> bool:
    """The arrow stands when Haiku, not told it, reaches the same one for a report about him."""
    return bool(answer.get("about_him")) and answer.get("effect") == effect


def _queue(written: list) -> list[list]:
    at, t = notify.now(), datetime.now().astimezone()

    def add(cur):
        waiting = [p for p in cur or [] if t - datetime.fromisoformat(p[2]) <= notify.MAX_AGE]
        known = {(sid, rev) for sid, rev, _ in waiting}
        return waiting + [[sid, rev, at] for sid, rev in written if (sid, rev) not in known]
    return db.update_state(QUEUE, add, [])


def run(written: list, index: Index, run_cli=claude_cli.run) -> dict:
    """`checked_ids`: the waiting revisions now safe to push, this run's `written` among them. One with nothing that could
    push needs no call; one whose story moved on is dropped, its next revision being checked in its place. A failed call
    ends the run (`error`); what it did not answer waits."""
    out = {"checked_ids": [], "flagged": 0, "waiting": 0, "error": None, "usage": usage.zero()}
    jobs: list[dict] = []
    done: set[tuple[int, int]] = set()
    want: dict[tuple[int, int], int] = {}
    for sid, rev, _ in _queue(written):
        s = db.story(sid)
        if s is None or s["rev"] != rev or s["written_rev"] != rev:
            done.add((sid, rev))
            continue
        rows = _rows(s) if s["items"] else []
        want[(sid, rev)] = len(rows)
        if not rows:
            done.add((sid, rev))
            out["checked_ids"].append((sid, rev))
        for p in rows:
            info, r = index.info(p["player_id"]), report(s, p["player_id"])
            jobs.append({"sid": sid, "rev": rev, "pid": p["player_id"], "effect": p["effect"], "name": info.get("name") or "?",
                         "pos": info.get("pos") or "?", "team": info.get("team"), "title": r["title"], "text": r["text"]})
    answered: dict[tuple[int, int], int] = {}
    for at in range(0, len(jobs), BATCH):
        batch = list(enumerate(jobs[at:at + BATCH]))
        try:
            got = run_cli(prompt(batch, index), model=MODEL, schema=SCHEMA, tools="", cwd=CWD, timeout=TIMEOUT, kind="news_check")
        except Exception as e:  # noqa: BLE001 — any failure of the CLI holds these pushes for the next run
            out["error"] = str(e)
            break
        bill = getattr(got, "usage", None) or {}
        usage.add(out["usage"], bill.get("input", 0), bill.get("output", 0))
        by_n = {a["n"]: a for a in got.get("answers") or [] if isinstance(a, dict) and "n" in a}
        for n, job in batch:
            a = by_n.get(n)
            if a is None:
                continue
            flag = not holds(a, job["effect"])
            db.set_story_flag(job["sid"], job["pid"], flag)
            out["flagged"] += flag
            key = (job["sid"], job["rev"])
            answered[key] = answered.get(key, 0) + 1
    for key, n in answered.items():
        if n == want[key]:
            done.add(key)
            out["checked_ids"].append(key)
    db.update_state(QUEUE, lambda cur: [p for p in cur or [] if (p[0], p[1]) not in done], [])
    out["waiting"] = len(want) - len(out["checked_ids"])
    return out
