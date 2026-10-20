"""Haiku writes each story's headline, overview and a line per player it moves (▲/▼) explaining Jev's arrow;
a player it only names (●) gets no line.

Jev decides the effect (▲ up / ▼ down / ● none); Haiku only words it, from the sources, and says when
the sources do not support the arrow (`disagree`, stored as the player's flag). A story is a situation that
develops (limps off, doubtful, out, on IR), so Haiku reads every source in full, newest first, and the headline
says where it stands now. The story is written from the rev it was read at: one that moved on meanwhile is
dropped at the write and read again next run. A headline refresh (Jev found the written headline stale, the rev
unmoved) is written the same way but kept out of `written_ids`, which the pushes are queued from.
A raised batch stops the run: the subscription's limit is the likely cause, and the rest would fail too.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import claude_cli, db
from ..config import DATA
from . import usage
from .players import Index

BATCH = 8
MODEL = "haiku"
TIMEOUT = 180
HEADLINE = 90
LINE = 140
SOURCE_CHARS = 6000  # chars of any one source's text
STORY_CHARS = 24000  # chars of source text a story: the newest sources fill it, older ones go in by title
ARROW = {"up": "up (▲)", "down": "down (▼)", "none": "none (●)"}

SCHEMA = {"type": "object", "required": ["stories"], "properties": {"stories": {"type": "array", "items": {
    "type": "object", "required": ["id", "headline", "overview", "players"], "properties": {
        "id": {"type": "integer"}, "headline": {"type": "string"}, "overview": {"type": "string"},
        "players": {"type": "array", "items": {
            "type": "object", "required": ["player_id", "reason", "disagree"], "properties": {
                "player_id": {"type": "integer"}, "reason": {"type": "string"}, "disagree": {"type": "boolean"}}}}}}}}}

INSTRUCTIONS = f"""You write the news desk's cards for a fantasy football app. A story is often a situation that
develops over several reports (limps off, then doubtful, then out, then on injured reserve). Its sources are listed
newest first, each with its outlet and published time; older ones past the length limit are listed by title only.
For each story below, give:
- headline: at most {HEADLINE} characters, plain: where things stand now, from the newest source. Never an earlier
  step the newer sources have moved past.
- overview: at most two sentences, carrying how it got there.
- players: for every listed player, one line of at most {LINE} characters explaining why the model's effect on
  that player's fantasy value is what it is, as things stand now. The effect is the model's call; explain it, from
  the sources only. Do not invent facts, dates or numbers that the sources lack. `disagree: true` when the sources
  do not support that effect (say why in the line); otherwise false. A story with no players listed takes an
  empty list.
A story whose status is corrected or retracted must say so plainly in both the headline and the overview
(for example "Correction: ..." or "Report withdrawn: ..."), naming the status note's source when given.
Answer for every story id given."""


def _moved(s: dict) -> list[dict]:
    """The players the story moves (▲/▼), the only ones given a line: one it merely names (●) is not its subject."""
    return [p for p in s["players"] if p["effect"] != "none"]


def _when(at: str) -> str:
    try:
        return datetime.fromisoformat(at).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return at


def _sources(s: dict, sources: dict[str, dict]) -> list[str]:
    """The live sources (every one, for a retracted story), newest first, each text cut at SOURCE_CHARS, until the
    story's STORY_CHARS are spent; the older rest by title only, in time order."""
    items = [i for i in s["items"] if not i.get("withdrawn_at")] or s["items"]
    left, out, rest = STORY_CHARS, [], []
    for n, i in enumerate(sorted(items, key=lambda i: (i["published_at"], i["id"]), reverse=True), 1):
        if left <= 0:
            rest.append(i)
            continue
        text = (i["text"] or "")[:min(SOURCE_CHARS, left)]
        left -= len(text)
        name = (sources.get(i["source_id"]) or {}).get("name") or i["source_id"]
        out += [f"Source {n}{' (newest)' if n == 1 else ''}: {name}, {_when(i['published_at'])}: {i['title']}", text]
    if rest:
        out.append("Earlier sources, by title only (oldest first):")
        out += [f"- {_when(i['published_at'])}: {i['title']}" for i in reversed(rest)]
    return out


def _story(s: dict, index: Index, sources: dict[str, dict]) -> str:
    note = f"; note: {s['status_note']}" if s.get("status_note") else ""
    out = [f"## Story {s['id']} (status: {s['status']}{note})", *_sources(s, sources)]
    out.append("Players:")
    for p in _moved(s):
        info = index.by_id.get(p["player_id"]) or {}
        out.append(f"- player_id {p['player_id']}: {info.get('name', '?')}, {info.get('pos', '?')}, {info.get('team', '?')}; "
                   f"effect {ARROW.get(p['effect'], p['effect'])}, kind {p['kind']}")
    return "\n".join(out)


def prompt(stories: list[dict], index: Index, sources: dict[str, dict] | None = None) -> str:
    """`sources`: id -> news_sources row, for the outlets' names (the id stands in for one unknown)."""
    return INSTRUCTIONS + "\n\n" + "\n\n".join(_story(s, index, sources or {}) for s in stories)


def _trim(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _put(s: dict, w: dict | None) -> bool:
    """Store Haiku's words for a story, unless they are incomplete: no headline or overview, or a ▲/▼ player left
    without a line (his push needs one). Those, like a story Haiku skipped, stay unwritten for the next run."""
    if w is None:
        return False
    moved = {p["player_id"] for p in _moved(s)}
    lines = {int(x["player_id"]): (_trim(x["reason"], LINE), bool(x["disagree"]))
             for x in w.get("players") or [] if int(x["player_id"]) in moved}
    headline, overview = _trim(w["headline"], HEADLINE), (w["overview"] or "").strip()
    if not headline or not overview or any(not (lines.get(pid) or ("",))[0] for pid in moved):
        return False
    return db.put_story_words(s["id"], s["rev"], headline, overview, lines, reword=s.get("reword") or 0)


def run(index: Index, run_cli=claude_cli.run) -> dict:
    """`written_ids`: the (story, rev) written for a new rev, which may push; `refreshed`: the stories written again at
    the same rev only because Jev found the headline stale, which never do. `usage` is what Haiku read and wrote this run, added to
    the day's in state `news_haiku_usage`."""
    out = {"written": 0, "written_ids": [], "refreshed": 0, "missing": 0, "failed": False, "error": None,
           "usage": usage.zero()}
    stories = db.unwritten_stories()  # all of them, newest first: no cap a run (Jonathan, 2026-10-19)
    sources = {s["id"]: s for s in db.news_sources(active_only=False)}
    for at in range(0, len(stories), BATCH):
        batch = stories[at:at + BATCH]
        try:
            got = run_cli(prompt(batch, index, sources), model=MODEL, schema=SCHEMA, tools="", cwd=DATA / "newsdesk",
                          timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001 — any failure of the CLI stops the run, with its text
            out["failed"], out["error"] = True, str(e)
            out["missing"] += len(stories) - at
            break
        bill = getattr(got, "usage", None) or {}
        usage.add(out["usage"], bill.get("input", 0), bill.get("output", 0))
        answered = {w["id"]: w for w in (got.get("stories") or []) if isinstance(w, dict) and "id" in w}
        for s in batch:
            try:
                ok = _put(s, answered.get(s["id"]))
            # one story's odd answer or failed write costs only that story: it is left unwritten and retried
            except (KeyError, TypeError, ValueError, AttributeError, db.DBError) as e:
                print(f"news write: story {s['id']}: {type(e).__name__}: {e}")
                ok = False
            if not ok:
                out["missing"] += 1
                continue
            out["written"] += 1
            if s["written_rev"] < s["rev"]:
                out["written_ids"].append((s["id"], s["rev"]))
            else:
                out["refreshed"] += 1
    try:
        usage.record(usage.HAIKU, out["usage"], datetime.now(timezone.utc))
    except db.DBError as e:
        print(f"news write: usage not recorded: {e}")
    return out
