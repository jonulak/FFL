"""Claude-written weekly outlooks, the ones every page reads (Roster, Team, Players), stored per week in the DB under a hash of the facts.

This module writes a player's bullets — injury, usage, depth, team news and a take: what the card's
numbers cannot say. The sheet is league-neutral (no start/bench call, no points). It holds
the card's non-projection facts plus news.gather's context (the news desk's stories, depth, injuries); the CLI
has no tools, so it writes from the sheet alone, and every bullet cites the sources the sheet names beside each
fact. Nothing here runs on a GET: generate() is started by
POST /outlook/refresh in a background thread, and the page reads the week's entries from the DB.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil  # noqa: F401  (tests/test_outlook.py patches outlook.shutil / outlook.subprocess; the modules are claude_cli's too)
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from . import claude_cli, db, news

HOOKS_OFF = claude_cli.HOOKS_OFF  # moved to claude_cli; still importable from here

OUTLOOK_DIR = claude_cli.neutral_cwd("outlook")  # only the neutral cwd for `claude -p` (the outlooks live in the DB)
# Sonnet over Opus (2026-10-20, the whole Week 7 roster through both): the same facts read right, ~11.6 s a card
# against ~15.4 s, and Sonnet the clearer where sources disagreed
MODEL = "sonnet"
# no web search: the news desk has already collected and tagged the news (news.fetch_desk), so a call only writes
TIMEOUT_S = 180  # one player alone; a batch gets 30 + 20 s a player (run_batch)
TOOLS = ""
LABELS = ("Injury", "Usage", "Depth", "Team", "Take")
_SOURCE = {"type": "object", "properties": {"site": {"type": "string"}, "date": {"type": "string"},
                                             "url": {"type": "string"}}, "required": ["site", "date", "url"]}
SCHEMA = {"type": "object", "required": ["bullets"], "properties": {"bullets": {"type": "array", "items": {
    "type": "object", "required": ["label", "text", "sources"], "properties": {
        "label": {"type": "string", "enum": list(LABELS)}, "text": {"type": "string"},
        "sources": {"type": "array", "minItems": 1, "items": _SOURCE}}}}}}
BATCH_SCHEMA = {"type": "object", "required": ["players"], "properties": {"players": {"type": "array", "items": {
    "type": "object", "required": ["player_id", "bullets"], "properties": {
        "player_id": {"type": "integer"}, "bullets": SCHEMA["properties"]["bullets"]}}}}}
GAMELOG = "https://www.espn.com/nfl/player/gamelog/_/id/{pid}"
INJURY_REPORT = "https://www.espn.com/nfl/injuries"
SLEEPER = "https://sleeper.com"
TAKE = "Take: your own read of what it all means for him; always include it. "
VOICE = ("Write this player's outlook as bullets labelled Injury, Usage, Depth, Team and Take, in that order. "
         "Injury: only when he is on the injury report, missed or was limited in practice this week, or is "
         "working back from an injury — then his status, practice reports and return timeline; a healthy "
         "player gets no Injury bullet. Usage: the trend across his recent games "
         "in snaps, targets, receptions and carries. Depth: who is ahead of and behind him, and who is hurt, "
         "with each hurt teammate's expected return week and its source, or that none has been reported. "
         "Team: teammate or scheme news that changes his week, such as his quarterback out, a coordinator "
         "fired or a receiver traded. " + TAKE + "Leave out any other label with nothing to report: never a bullet that only says there is no news. One or two sentences, at most thirty words, per "
         "bullet. Write from the facts above alone and add nothing they do not say; say so when sources "
         "disagree, and prefer the newest. Use nothing older than ten days unless it is the latest word on an injury that is still "
         "active, and give the date of any injury news. Never quote projected or expected fantasy points. "
         "Every bullet cites at least one source with its site, date "
         "(M/D) and URL; a fact from this sheet cites the source named beside it. Third person, written to "
         "his manager.")
LOCK = threading.Lock()
# the last run over every league: its summed counts, and each league that could not be read (`errors`)
LAST: dict = {"at": None, "generated": 0, "skipped": 0, "failed": 0, "retried": 0, "pending": None,
              "last_failure": None, "errors": []}


def last() -> dict:
    """The last run's counts."""
    return LAST


# Who gets an outlook: every rostered skill player, every watched player, and a free agent only when the news or the
# scoreboard has made him interesting (snap share alone pulled in every backup: ~350 of ~550 free agents a league).
POSITIONS = ("QB", "RB", "WR", "TE")
FA_NEWS_DAYS = 7  # an ▲ story this recent
FA_LAST_PTS = 10  # scored this in his last game
FA_L3_PTS = 8  # or averaged this over his last three


def recent_up(player_ids: list[int], today: date) -> set[int]:
    """Ids with an ▲ story in the last FA_NEWS_DAYS days."""
    since = (today - timedelta(days=FA_NEWS_DAYS)).isoformat()
    return {pid for pid, stories in db.player_stories(player_ids, since).items()
            if any(r.get("player_id") == pid and r.get("effect") == "up" for s in stories for r in s.get("players", []))}


def _hot(p: dict) -> bool:
    form = p.get("form") or {}
    rows = form.get("rows") or []
    l3 = form.get("l3")
    return bool(rows and (rows[-1].get("pts") or 0) >= FA_LAST_PTS) or (l3 is not None and l3 >= FA_L3_PTS)


def wanted(pool: dict, up: set[int], watched: set[int]) -> list[dict]:
    """The rows to write an outlook for: rostered first (pool order), then qualifying free agents; one per player."""
    rows = [p for p in pool["rostered"] if p["pos"] in POSITIONS]
    rows += [p for pos, ps in pool["frees"].items() if pos in POSITIONS for p in ps
             if p["player_id"] in watched or p["player_id"] in up or _hot(p)]
    seen: set[int] = set()
    return [p for p in rows if not (p["player_id"] in seen or seen.add(p["player_id"]))]


# calls in flight at once: a full rewrite is ~15 cards of ~15 s each, so the whole roster goes at once. Lower it if
# the Claude plan starts refusing calls (they show as failures with the CLI's message in last_failure).
# FFL_OUTLOOK_WORKERS overrides it (the box, with 2 GB, runs 3); read at import, so set it before the API starts.
WORKERS = int(os.environ.get("FFL_OUTLOOK_WORKERS") or 20)  # an empty line in the env file counts as unset


class Busy(RuntimeError):
    """A generation run is already in progress."""


def _sha(facts) -> str:
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()[:16]


def _rows(card: dict) -> list[dict]:
    return (card.get("form") or {}).get("rows") or []


def card_hash(card: dict) -> str:
    """The card's own facts the bullets rest on — what the page can check on a GET without fetching
    news. Projections are not among them: the outlook no longer mentions them."""
    rows = [[r.get("week"), r.get("snap_pct"), r.get("tgt"), r.get("car"), r.get("rec")] for r in _rows(card)]
    return _sha([card.get("opponent"), card.get("kickoff"), bool(card.get("bye")), card.get("injury"),
                 card.get("injury_note"), card.get("return_week"), rows])


def fact_hash(card: dict, ctx: dict) -> str:
    """card_hash plus the news (his depth, the teammates' status and stories, his own stories) and the voice rule,
    so a changed prompt reaches the cards written under the old one. No date: same facts, same outlook."""
    mates = [[m.get("name"), m.get("status"), m.get("note"), _story_keys(m)] for m in ctx.get("teammates") or []]
    return _sha([card_hash(card), ctx.get("depth"), mates, _story_keys(ctx), VOICE])


def _story_keys(holder: dict) -> list:
    """A news desk story moves the hash when it is new, gains a source or is rewritten (rev), or is corrected."""
    return [[s.get("id"), s.get("rev"), s.get("status"), len(s.get("sources") or [])] for s in holder.get("stories") or []]


def _md(published: str | None) -> str:
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone().strftime("%-m/%-d")
    except (AttributeError, ValueError):
        return "undated"


def _exp_text(card: dict) -> str:
    exp = card.get("exp")
    return "years in the league unknown" if exp is None else "rookie" if exp == 0 else f"{exp} years in the league"


def _row_text(r: dict) -> str:
    parts = [f"{r['snap_pct']}% snaps" if r.get("snap_pct") is not None else "snaps not in yet"]
    for key, label in (("tgt", "targets"), ("rec", "receptions"), ("car", "carries"), ("pass_att", "pass attempts"),
                       ("air_yd", "completed air yards"), ("rz_tgt", "red-zone targets"), ("rz_att", "red-zone carries"),
                       ("rz_pass", "red-zone pass attempts")):
        if r.get(key):
            parts.append(f"{r[key]:g} {label}")
    return f"  Week {r['week']}: " + ", ".join(parts)


def _depth_text(d: dict | None) -> str:
    if d is None:
        return "not on Sleeper's chart"
    return f"{d['rank']} of {d['of']} at his position (Sleeper slot {d['slot']} #{d['order']})"


def _mate_text(m: dict) -> str:
    s = f"  {m['pos']}{m['rank']} {m['name']}: {m['status']}"
    if m.get("note"):
        s += f" — {m['note']}"
    for st in m.get("stories") or []:
        s += f"\n    {_story_text(st)}"
    return s


EFFECT = {"up": "▲", "down": "▼", "none": "●"}


def _story_text(s: dict) -> str:
    """One news desk story: date, effect on him, the desk's write-up (or the start of its first source), his line,
    and every live source with its own date and link."""
    body = " ".join(x for x in (s.get("headline"), s.get("overview") or s.get("text")) if x)
    if s.get("line"):
        body += f" For him: {s['line']}"
    corrected = " (since corrected)" if s.get("status") == "corrected" else ""
    # the cites stay outside the block, unescaped: the model copies each URL into its bullet's sources
    cites = "; ".join(f"{c['site']} {_md(c.get('published'))} {c['url']}" for c in s.get("sources") or [])
    block = claude_cli.data_block("source", body, published=_md(s.get("published")),
                                  sites="; ".join(c["site"] for c in s.get("sources") or []) or None)
    return f"{_md(s.get('published'))} {EFFECT.get(s.get('effect'), '●')} {block}{corrected} (sources: {cites})"


def _kickoff_text(card: dict) -> str:
    """A missing kickoff is only a bye when the card says so — ESPN also leaves it empty
    for a player it has no schedule entry for, and calling that a bye puts a false fact on the sheet."""
    k = card.get("kickoff")
    if not k:
        return "no game (bye)" if card.get("bye") else "not scheduled"
    return datetime.fromisoformat(k).strftime("%A %-I:%M %p")


def _opponent_text(card: dict) -> str:
    o = card.get("opponent")
    if o:
        return o
    return "none (bye)" if card.get("bye") else "not scheduled"


def fact_sheet(card: dict, week: int, ctx: dict, today: date) -> str:
    """The prompt: the card's facts that are not projections, the news context, then the voice rule.
    Nothing league-specific: one sheet, and one hash, whichever league looks at the player."""
    lines = [
        f"Week {week} fact sheet — today is {today:%A} {today.month}/{today.day}/{today.year}",
        claude_cli.DATA_RULE,
        f"Player: {card['name']}, {card['pos']}, {card['team']}; {_exp_text(card)}",
        f"Opponent: {_opponent_text(card)}; kickoff: {_kickoff_text(card)}",
        f"Injury (source: ESPN injury report, {INJURY_REPORT}): {card.get('injury')}; "
        f"note: {card.get('injury_note') or 'none'}; return week: {card.get('return_week') or 'n/a'}",
        f"Recent games (source: ESPN game log, {GAMELOG.format(pid=card['player_id'])}):",
        *([_row_text(r) for r in _rows(card)] or ["  none yet"]),
        f"Depth (source: Sleeper depth chart, {SLEEPER}): {_depth_text(ctx.get('depth'))}",
        f"Teammates whose news matters to him, and every hurt teammate at his position with his news desk news "
        f"(source: ESPN injury report, {INJURY_REPORT}):",
        *([_mate_text(m) for m in ctx.get("teammates") or []] or ["  none in scope"]),
        "News desk stories naming him, newest first (▲ helps him, ▼ hurts him, ● only names him; each cites its "
        "own sources):",
        *([f"  {_story_text(s)}" for s in ctx.get("stories") or []] or ["  none"]),
        "",
        VOICE,
    ]
    return "\n".join(lines)


def load(week: int) -> dict[int, dict]:
    """The week's entries by player id; a week with none is empty."""
    return db.outlooks_for(week)


def save(week: int, entries: dict[int, dict]) -> None:
    db.put_outlooks(week, entries)


def put(week: int, pid: int, entry: dict) -> None:
    db.put_outlooks(week, {pid: entry})


def _entry(entries: dict[int, dict], pid: int) -> tuple[dict, dict | None]:
    """(the raw entry or {}, the entry when it is a whole one, else None)."""
    raw = entries.get(pid)
    raw = raw if isinstance(raw, dict) else {}
    # a half-written entry, or a paragraph from before the bullets, is no entry: the next run rewrites it
    return raw, raw if "card_hash" in raw and isinstance(raw.get("bullets"), list) else None


def attach_plain(rows: list[dict], entries: dict[int, dict]) -> list[dict]:
    """The stored bullets on each row, with the last write's failure; no stale flag (a row is not a card)."""
    out = []
    for r in rows:
        raw, e = _entry(entries, r["player_id"])
        out.append({**r, "outlook": e["bullets"] if e else None, "outlook_at": e.get("generated_at") if e else None,
                    "outlook_error": raw.get("error")})
    return out


def attach(cards: list[dict], entries: dict[int, dict]) -> list[dict]:
    """The stored bullets on each card, flagged stale when the card's own facts have moved since, with the last
    write's failure."""
    out = []
    for c, a in zip(cards, attach_plain(cards, entries)):
        e = _entry(entries, c["player_id"])[1]
        out.append({**a, "outlook_stale": bool(e) and e["card_hash"] != card_hash(c)})
    return out


def normalize(raw) -> list[dict]:
    """The model's bullets in LABELS order. Unknown or repeated labels, empty text and bullets without a
    usable source are dropped; no Take is a failed call — it is the one bullet every card shows."""
    kept: dict[str, dict] = {}
    for b in (raw or {}).get("bullets") or []:
        if not isinstance(b, dict):
            continue
        label, text = b.get("label"), str(b.get("text") or "").strip()
        sources = [{k: str(s.get(k) or "").strip() for k in ("site", "date", "url")}
                   for s in b.get("sources") or [] if isinstance(s, dict)]
        sources = [s for s in sources if s["site"] and s["url"].startswith(("http://", "https://"))]
        if label in LABELS and label not in kept and text and sources:
            kept[label] = {"label": label, "text": text, "sources": sources}
    if "Take" not in kept:
        raise RuntimeError("claude returned no Take")
    return [kept[k] for k in LABELS if k in kept]


def parse_envelope(stdout: str) -> list[dict]:
    """The bullets out of `claude -p --output-format json` (the envelope is claude_cli's)."""
    return normalize(claude_cli.parse_envelope(stdout))


def run_claude(prompt: str) -> list[dict]:
    """One outlook through the Claude Code CLI: MODEL with no tools, structured output, the sheet on stdin, run from the neutral
    OUTLOOK_DIR so the CLI reads no CLAUDE.md (the sheet is the whole prompt)."""
    return normalize(claude_cli.run(prompt, model=MODEL, schema=SCHEMA, tools=TOOLS, cwd=OUTLOOK_DIR,
                                    timeout=TIMEOUT_S, kind="outlook"))


# eight a call halved the time per card and cut tokens to a third, with no player lost (measured on roster cards)
BATCH_MAX = 8
BATCH_RULE = ("Write each player's outlook from his own sheet alone, never mixing facts across players; "
              "return one entry per player_id.")


def batches(todo: list[dict], workers: int) -> list[list[dict]]:
    """The cards in order, split so every worker has a batch before any batch passes BATCH_MAX."""
    size = max(1, min(BATCH_MAX, math.ceil(len(todo) / workers)))
    return [todo[i:i + size] for i in range(0, len(todo), size)]


def batch_prompt(sheets: list[tuple[int, str]]) -> str:
    """Each player's sheet under his id, the voice rule once at the end rather than once a sheet."""
    tail = "\n\n" + VOICE
    body = "\n\n".join(f"=== player_id {pid} ===\n{s.removesuffix(tail)}" for pid, s in sheets)
    return f"{body}\n\n{BATCH_RULE} {VOICE}"


def run_batch(prompt: str, player_ids: list[int]) -> dict[int, list[dict]]:
    """Several outlooks in one CLI call, by player id. An id not asked for is dropped, a repeated one's
    first answer kept, and a player whose bullets fail normalize is left out for generate() to retry alone."""
    raw = claude_cli.run(prompt, model=MODEL, schema=BATCH_SCHEMA, tools=TOOLS, cwd=OUTLOOK_DIR,
                         timeout=30 + 20 * len(player_ids), kind="outlook")
    out: dict[int, list[dict]] = {}
    for e in (raw or {}).get("players") or []:
        pid = e.get("player_id") if isinstance(e, dict) else None
        if pid not in player_ids or pid in out:
            continue
        try:
            out[pid] = normalize(e)
        except RuntimeError:
            pass
    return out


def pending(cards: list[dict], entries: dict[int, dict], ctxs: dict[int, dict], today: date) -> list[dict]:
    """Cards a run would write: not played, a bye only with news (news.has_news), and no entry with the
    current hash. IR and OUT are written: the return timeline is the outlook they need."""
    out = []
    for c in cards:
        ctx, old = ctxs[c["player_id"]], entries.get(c["player_id"])
        if c.get("game_played") or (c.get("bye") and not news.has_news(c, ctx, today)):
            continue
        if isinstance(old, dict) and old.get("hash") == fact_hash(c, ctx):
            continue
        out.append(c)
    return out


def generate(cards: list[dict], week: int, run=run_batch, single=run_claude, gather=None,
             now: datetime | None = None, today: date | None = None) -> dict:
    """Write bullets for every pending card, in batches (`run`), WORKERS batches at a time. A player the
    batch left out, or every player of a batch that raised, is written alone (`single`), until one of those
    times out (the rest of that batch then fail uncalled). Each finished
    card is saved at once, so a run that dies keeps what it wrote; a failed call keeps the old entry.
    `gather` defaults to news.gather, looked up here so a test can replace it."""
    today = today or date.today()
    ctxs = (gather or news.gather)(cards, week)
    entries = load(week)
    todo = pending(cards, entries, ctxs, today)
    counts: dict = {"generated": 0, "skipped": len(cards) - len(todo), "failed": 0, "retried": 0,
                    "last_failure": None, "pending": len(todo)}
    lock = threading.Lock()  # entries and counts are shared by the workers

    def land(c: dict, bullets: list[dict]) -> None:
        stamp = (now or datetime.now().astimezone()).isoformat(timespec="minutes")
        with lock:
            entries[c["player_id"]] = {"hash": fact_hash(c, ctxs[c["player_id"]]), "card_hash": card_hash(c),
                                       "bullets": bullets, "generated_at": stamp}
            put(week, c["player_id"], entries[c["player_id"]])
            counts["generated"] += 1

    def write(c: dict, bullets) -> None:
        try:
            land(c, bullets())
        except Exception as e:  # noqa: BLE001 — a missing CLI, a timeout or bad JSON costs one card
            fail(e)

    def fail(e: Exception, n: int = 1) -> None:
        with lock:
            counts["failed"] += n
            note(e)

    def note(e: Exception) -> None:  # under lock; the run survives, so without this the reason is lost
        if counts["last_failure"] is None:
            counts["last_failure"] = f"{type(e).__name__}: {e}"[:200]

    def one(batch: list[dict]) -> None:
        try:
            sheets = {c["player_id"]: fact_sheet(c, week, ctxs[c["player_id"]], today) for c in batch}
        except Exception as e:  # noqa: BLE001 — a card the sheet cannot read costs its batch, not the run
            return fail(e, len(batch))
        try:
            got = run(batch_prompt(list(sheets.items())), list(sheets))
        except Exception as e:  # noqa: BLE001 — a timeout or bad JSON: every player gets his own call
            with lock:
                note(e)
            got = {}
        missing = []
        for c in batch:  # the batch's answers saved before any retry starts
            if c["player_id"] in got:
                write(c, lambda: got[c["player_id"]])
            else:
                missing.append(c)
        for i, c in enumerate(missing):
            with lock:
                counts["retried"] += 1
            try:
                land(c, single(sheets[c["player_id"]]))
            except subprocess.TimeoutExpired as e:  # the CLI is hung: the rest would each wait out the timeout too
                return fail(e, len(missing) - i)
            except Exception as e:  # noqa: BLE001 — a missing CLI or bad JSON costs one card
                fail(e)

    with ThreadPoolExecutor(WORKERS) as pool:
        for f in as_completed([pool.submit(one, b) for b in batches(todo, WORKERS)]):
            f.result()
    return counts


def running() -> bool:
    return LOCK.locked()


def start_all(jobs: list, run=run_batch, single=run_claude) -> threading.Thread:
    """One run over every league on a daemon thread under LOCK; Busy when one is already running. Each job reads
    its league's (cards, week) inside the thread; one that raises is logged in LAST["errors"] and the rest still
    run. A player in several leagues is written once a week (first seen wins): the outlook is league-neutral.

    POST /outlook/refresh cannot wait: a week rollover is many calls, WORKERS at a time, and the page's fetch
    gives up at twenty seconds. last() carries the finished run's counts.
    """
    if not LOCK.acquire(blocking=False):
        raise Busy("outlook refresh already running")

    def work():
        # Nothing above this thread can see an exception it raises, so every failure lands in `errors`
        errors: list[str] = []
        total = {"generated": 0, "skipped": 0, "failed": 0, "retried": 0, "pending": 0, "last_failure": None}
        try:
            by_week: dict[int, dict[int, dict]] = {}
            for job in jobs:
                try:
                    cards, week = job()
                    # keyed inside the try: a card without an id costs its league's job, not the run
                    keyed = [(c["player_id"], c) for c in cards]
                except Exception as e:  # noqa: BLE001 — the page needs the reason, not a lost traceback
                    errors.append(f"{type(e).__name__}: {e}")
                    continue
                rows = by_week.setdefault(week, {})
                for pid, c in keyed:
                    rows.setdefault(pid, c)
            for week, rows in by_week.items():
                try:
                    counts = generate(list(rows.values()), week, run=run, single=single)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{type(e).__name__}: {e}")
                    continue
                for k in ("generated", "skipped", "failed", "retried", "pending"):
                    total[k] += counts[k]
                total["last_failure"] = total["last_failure"] or counts["last_failure"]
            LAST.update(total, at=datetime.now().astimezone().isoformat(timespec="minutes"), errors=errors)
            print(f"outlook run: {total}, errors: {errors}")
        finally:
            LOCK.release()

    t = threading.Thread(target=work, name="outlook", daemon=True)
    try:
        t.start()
    except Exception:  # noqa: BLE001 — a thread that never ran will never release the lock
        LOCK.release()
        raise
    return t


def written(week: int) -> set[int]:
    """The players with bullets this week, for the Players table's button."""
    return {pid for pid, e in load(week).items() if isinstance(e, dict) and isinstance(e.get("bullets"), list)}
