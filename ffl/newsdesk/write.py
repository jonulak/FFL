"""Haiku writes each story's headline, overview and a line per player explaining Jev's arrow.

Jev decides the effect (▲ up / ▼ down / ● none); Haiku only words it, from the sources, and says when
the sources do not support the arrow (`disagree`, stored as the player's flag). The story is written from
the rev it was read at: one that moved on meanwhile is dropped at the write and read again next run.
A raised batch stops the run: the subscription's limit is the likely cause, and the rest would fail too.
"""
from __future__ import annotations

from .. import claude_cli, db
from ..config import DATA
from . import tag
from .players import Index

BATCH = 8
MODEL = "haiku"
TIMEOUT = 180
HEADLINE = 90
LINE = 140
MAX_TEXT = 6000  # chars of the first source; it is the only one read in full
ARROW = {"up": "up (▲)", "down": "down (▼)", "none": "none (●)"}

SCHEMA = {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array", "items": {
    "type": "object", "required": ["id", "headline", "overview", "players"], "properties": {
        "id": {"type": "integer"}, "headline": {"type": "string"}, "overview": {"type": "string"},
        "players": {"type": "array", "items": {
            "type": "object", "required": ["player_id", "reason", "disagree"], "properties": {
                "player_id": {"type": "integer"}, "reason": {"type": "string"}, "disagree": {"type": "boolean"}}}}}}}}}

INSTRUCTIONS = f"""You write the news desk's cards for a fantasy football app. For each story below, give:
- headline: at most {HEADLINE} characters, plain, what happened.
- overview: at most two sentences.
- players: for every listed player, including those marked none (●), one line of at most {LINE} characters
  explaining why the model's effect on that player's fantasy value is what it is. The effect is the model's
  call; explain it, from the sources only. Do not invent facts, dates or numbers that the sources lack.
  `disagree: true` when the sources do not support that effect (say why in the line); otherwise false.
An effect of none (●) is often a player merely named: in a recap or preview of news already known, or as a
side mention. When the model marked it a passing mention of known news, say so ("only mentioned in a recap
of his known injury"); do not make news of it.
A story whose status is corrected or retracted must say so plainly in both the headline and the overview
(for example "Correction: ..." or "Report withdrawn: ..."), naming the status note's source when given.
Answer for every story id given."""


def _passing(story: dict, pid: int) -> bool:
    """Did Jev judge an item of the story a passing mention of known news for this player?"""
    key = f"fresh:{pid}"
    return any(((i.get("jev") or {}).get(key) or {}).get("noul", 1.0) < tag.FRESH for i in story["items"])


def _story(s: dict, index: Index) -> str:
    items = s["items"]
    note = f"; note: {s['status_note']}" if s.get("status_note") else ""
    out = [f"## Story {s['id']} (status: {s['status']}{note})"]
    if items:
        out += [f"Source 1: {items[0]['title']}", (items[0]["text"] or "")[:MAX_TEXT]]
        out += [f"Source {n}: {i['title']}" for n, i in enumerate(items[1:], 2)]
    out.append("Players:")
    for p in s["players"]:
        info = index.by_id.get(p["player_id"]) or {}
        line = (f"- player_id {p['player_id']}: {info.get('name', '?')}, {info.get('pos', '?')}, {info.get('team', '?')}; "
                f"effect {ARROW.get(p['effect'], p['effect'])}, kind {p['kind']}")
        if p["effect"] == "none" and _passing(s, p["player_id"]):
            line += "; the model judged this a passing mention of news already known"
        out.append(line)
    return "\n".join(out)


def prompt(stories: list[dict], index: Index) -> str:
    return INSTRUCTIONS + "\n\n" + "\n\n".join(_story(s, index) for s in stories)


def _trim(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _put(s: dict, w: dict | None) -> bool:
    """Store Haiku's words for a story, unless they are incomplete: no headline or overview, or a ▲/▼ player left
    without a line (his push needs one). Those, like a story Haiku skipped, stay unwritten for the next run."""
    if w is None:
        return False
    mine = {p["player_id"] for p in s["players"]}
    lines = {int(x["player_id"]): (_trim(x["reason"], LINE), bool(x["disagree"]))
             for x in w.get("players") or [] if int(x["player_id"]) in mine}
    headline, overview = _trim(w["headline"], HEADLINE), (w["overview"] or "").strip()
    if not headline or not overview or any(p["effect"] != "none" and not (lines.get(p["player_id"]) or ("",))[0]
                                           for p in s["players"]):
        return False
    return db.put_story_words(s["id"], s["rev"], headline, overview, lines)


def run(index: Index, run_cli=claude_cli.run) -> dict:
    out = {"written": 0, "written_ids": [], "missing": 0, "failed": False, "error": None}
    stories = db.unwritten_stories()  # all of them, newest first: no cap a run (Jonathan, 2026-10-19)
    for at in range(0, len(stories), BATCH):
        batch = stories[at:at + BATCH]
        try:
            got = run_cli(prompt(batch, index), model=MODEL, schema=SCHEMA, tools="", cwd=DATA / "newsdesk",
                          timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001 — any failure of the CLI stops the run, with its text
            out["failed"], out["error"] = True, str(e)
            out["missing"] += len(stories) - at
            break
        answered = {w["id"]: w for w in (got.get("stories") or []) if isinstance(w, dict) and "id" in w}
        for s in batch:
            try:
                ok = _put(s, answered.get(s["id"]))
            # one story's odd answer or failed write costs only that story: it is left unwritten and retried
            except (KeyError, TypeError, ValueError, AttributeError, db.DBError) as e:
                print(f"news write: story {s['id']}: {type(e).__name__}: {e}")
                ok = False
            if ok:
                out["written"] += 1
                out["written_ids"].append((s["id"], s["rev"]))
            else:
                out["missing"] += 1
    return out
