"""Claude-written weekly outlooks for the Roster page, stored per week in the DB under a hash of the facts.

The call and its reason are code (season.roster_cards). This module writes the bullets under them —
injury, usage, depth, team news and a take: what the card's numbers cannot say. The fact sheet holds
the card's non-projection facts plus news.gather's context (the news desk's stories, depth, injuries); the CLI
has no tools, so it writes from the sheet alone, and every bullet cites the sources the sheet names beside each
fact. Nothing here runs on a GET: generate() is started by
POST /outlook/refresh in a background thread, and the page reads the week's entries from the DB.
"""
from __future__ import annotations

import hashlib
import json
import shutil  # noqa: F401  (tests/test_outlook.py patches outlook.shutil / outlook.subprocess; the modules are claude_cli's too)
import subprocess  # noqa: F401
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

from . import claude_cli, db, news

HOOKS_OFF = claude_cli.HOOKS_OFF  # moved to claude_cli; still importable from here

OUTLOOK_DIR = claude_cli.neutral_cwd("outlook")  # only the neutral cwd for `claude -p` (the outlooks live in the DB)
# Sonnet over Opus (2026-10-20, the whole Week 7 roster through both): the same facts read right, ~11.6 s a card
# against ~15.4 s, and Sonnet the clearer where sources disagreed
MODEL = "sonnet"
# no web search: the news desk has already collected and tagged the news (news.fetch_desk), so a call only writes
TIMEOUT_S = 180
TOOLS = ""
LABELS = ("Injury", "Usage", "Depth", "Team", "Take")
_SOURCE = {"type": "object", "properties": {"site": {"type": "string"}, "date": {"type": "string"},
                                             "url": {"type": "string"}}, "required": ["site", "date", "url"]}
SCHEMA = {"type": "object", "required": ["bullets"], "properties": {"bullets": {"type": "array", "items": {
    "type": "object", "required": ["label", "text", "sources"], "properties": {
        "label": {"type": "string", "enum": list(LABELS)}, "text": {"type": "string"},
        "sources": {"type": "array", "minItems": 1, "items": _SOURCE}}}}}}
GAMELOG = "https://www.espn.com/nfl/player/gamelog/_/id/{pid}"
INJURY_REPORT = "https://www.espn.com/nfl/injuries"
SLEEPER = "https://sleeper.com"
# The Take is the one rule a free agent's sheet changes: there is no call to read, only a claim to weigh.
TAKE = "Take: your own read of what it all means for him; always include it. "
FA_TAKE = ("Take: what adding him would mean for his manager — a stash, a one-week fill-in or a starter, and "
           "for how long; always include it. ")
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
         "active, and give the date of any injury news. Never quote projected or expected fantasy points and "
         "do not restate the start or bench call. Every bullet cites at least one source with its site, date "
         "(M/D) and URL; a fact from this sheet cites the source named beside it. Third person, written to "
         "his manager.")
LOCK = threading.Lock()
# the last generation run, shown on the Roster page beside the button
LAST: dict = {"at": None, "generated": 0, "skipped": 0, "failed": 0, "error": None, "last_failure": None,
              "pending": None}
# calls in flight at once: a full rewrite is ~15 cards of ~15 s each, so the whole roster goes at once. Lower it if
# the Claude plan starts refusing calls (they show as failures with the CLI's message in last_failure).
WORKERS = 20


class Busy(RuntimeError):
    """A generation run is already in progress."""


def _sha(facts) -> str:
    return hashlib.sha256(json.dumps(facts).encode()).hexdigest()[:16]


def _rows(card: dict) -> list[dict]:
    return (card.get("form") or {}).get("rows") or []


def card_hash(card: dict) -> str:
    """The card's own facts the bullets rest on — what the page can check on a GET without fetching
    news. Projections are not among them: the outlook no longer mentions them."""
    rows = [[r.get("week"), r.get("snap_pct"), r.get("tgt"), r.get("car"), r.get("rec"), r.get("pts")] for r in _rows(card)]
    return _sha([card.get("opponent"), card.get("kickoff"), bool(card.get("bye")), card.get("injury"),
                 card.get("injury_note"), card.get("return_week"), card.get("model_call", card.get("call")), rows])


def fact_hash(card: dict, ctx: dict, today: date) -> str:
    """card_hash plus the news (his depth, the teammates' status and stories, his own stories), the day, so every
    outlook is rewritten at least once a day, and the voice rule, so a changed prompt reaches the cards written
    under the old one."""
    mates = [[m.get("name"), m.get("status"), m.get("note"), _story_keys(m)] for m in ctx.get("teammates") or []]
    return _sha([card_hash(card), ctx.get("depth"), mates, _story_keys(ctx), today.isoformat(), VOICE])


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
    return f"  Week {r['week']}: " + ", ".join(parts + [f"{r['pts']:g} pts"])


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
    if s.get("status") == "corrected":
        body += " (since corrected)"
    cites = "; ".join(f"{c['site']} {_md(c.get('published'))} {c['url']}" for c in s.get("sources") or [])
    return f"{_md(s.get('published'))} {EFFECT.get(s.get('effect'), '●')} {body} (sources: {cites})"


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


def fact_sheet(card: dict, week: int, ctx: dict, today: date, free_agent: bool = False) -> str:
    """The prompt: the card's facts that are not projections, the news context, then the voice rule.
    A free agent (a Waivers row) has no call: the sheet says a claim is being weighed and the Take reads
    what adding him would mean."""
    role = ("Roster status: free agent; his manager is weighing a claim on him" if free_agent
            else f"Start/bench call (background only, do not restate): {card.get('model_call', card['call'])}")
    lines = [
        f"Week {week} fact sheet — today is {today:%A} {today.month}/{today.day}/{today.year}",
        f"Player: {card['name']}, {card['pos']}, {card['team']}; {_exp_text(card)}",
        f"Opponent: {_opponent_text(card)}; kickoff: {_kickoff_text(card)}",
        role,
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
        VOICE.replace(TAKE, FA_TAKE) if free_agent else VOICE,
    ]
    return "\n".join(lines)


# The two stores: the Roster run's entries, and the free agents written on a click from Waivers. Kept
# apart so the hourly run's read-modify-write never meets a click's.
ROSTER, WAIVERS = "week", "waivers-week"


def load(week: int, kind: str = ROSTER) -> dict[int, dict]:
    """The week's entries by player id; a week with none is empty."""
    return db.outlooks_for(week, kind)


def save(week: int, entries: dict[int, dict], kind: str = ROSTER) -> None:
    db.put_outlooks(week, kind, entries)


def put(week: int, pid: int, entry: dict, kind: str = ROSTER) -> None:
    db.put_outlooks(week, kind, {pid: entry})


def attach(cards: list[dict], entries: dict[int, dict], writing: set[int] = frozenset()) -> list[dict]:
    """The stored bullets on each card, flagged stale when the card's own facts have moved since, with
    whether a one-card write is running for him and the last one's failure."""
    out = []
    for c in cards:
        raw = entries.get(c["player_id"])
        raw = raw if isinstance(raw, dict) else {}
        # a half-written entry, or a paragraph from before the bullets, is no entry: the next run rewrites it
        e = raw if "card_hash" in raw and isinstance(raw.get("bullets"), list) else None
        out.append({**c, "outlook": e["bullets"] if e else None, "outlook_at": e.get("generated_at") if e else None,
                    "outlook_stale": bool(e) and e["card_hash"] != card_hash(c),
                    "outlook_running": c["player_id"] in writing, "outlook_error": raw.get("error")})
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
                                    timeout=TIMEOUT_S))


def pending(cards: list[dict], entries: dict[int, dict], ctxs: dict[int, dict], today: date) -> list[dict]:
    """Cards a run would write: not played, a bye only with news (news.has_news), and no entry with the
    current hash. IR and OUT are written: the return timeline is the outlook they need."""
    out = []
    for c in cards:
        ctx, old = ctxs[c["player_id"]], entries.get(c["player_id"])
        if c.get("game_played") or (c.get("call") == "BYE" and not news.has_news(c, ctx, today)):
            continue
        if isinstance(old, dict) and old.get("hash") == fact_hash(c, ctx, today):
            continue
        out.append(c)
    return out


def generate(cards: list[dict], week: int, run=run_claude, gather=None, now: datetime | None = None,
             today: date | None = None) -> dict:
    """Write bullets for every pending card, WORKERS CLI calls at a time. Each finished card is saved
    at once, so a run that dies keeps what it wrote; a failed call keeps the old entry. `gather`
    defaults to news.gather, looked up here so a test can replace it."""
    today = today or date.today()
    ctxs = (gather or news.gather)(cards, week)
    entries = load(week)
    todo = pending(cards, entries, ctxs, today)
    counts: dict = {"generated": 0, "skipped": len(cards) - len(todo), "failed": 0, "last_failure": None,
                    "pending": len(todo)}
    lock = threading.Lock()  # entries and counts are shared by the workers

    def one(c: dict) -> None:
        ctx = ctxs[c["player_id"]]
        bullets = run(fact_sheet(c, week, ctx, today))
        stamp = (now or datetime.now().astimezone()).isoformat(timespec="minutes")
        with lock:
            entries[c["player_id"]] = {"hash": fact_hash(c, ctx, today), "card_hash": card_hash(c),
                                       "bullets": bullets, "generated_at": stamp}
            put(week, c["player_id"], entries[c["player_id"]])
            counts["generated"] += 1

    with ThreadPoolExecutor(WORKERS) as pool:
        for f in as_completed([pool.submit(one, c) for c in todo]):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 — a missing CLI, a timeout or bad JSON costs one card
                with lock:
                    counts["failed"] += 1
                    # the run survives, so LAST["error"] stays None; without this the reason is lost
                    if counts["last_failure"] is None:
                        counts["last_failure"] = f"{type(e).__name__}: {e}"[:200]
    return counts


def running() -> bool:
    return LOCK.locked()


def start(cards: list[dict], week: int, run=run_claude) -> threading.Thread:
    """Run generate() on a daemon thread under LOCK; Busy when one is already running.

    POST /outlook/refresh cannot wait: a week rollover is a roster of ~12 s calls, WORKERS at a time, and the
    page's fetch gives up at twenty seconds. LAST carries the finished run's counts to the page.
    """
    if not LOCK.acquire(blocking=False):
        raise Busy("outlook refresh already running")
    # generate() writes back the entries it loaded at its start, so a card written on its own
    # mid-run would be lost: the two never overlap (start_one refuses a card while LOCK is held)
    with _WLOCK:
        if writing(ROSTER):
            LOCK.release()
            raise Busy("a card's outlook is being written; try again when it lands")

    def work():
        # Nothing above this thread can see an exception it raises, so a run that dies has to leave
        # its own record: the counts stay at the last finished run's and `error` says this one failed.
        try:
            counts = generate(cards, week, run=run)
            LAST.update(counts, at=datetime.now().astimezone().isoformat(timespec="minutes"), error=None)
        except Exception as e:  # noqa: BLE001 — the page needs the reason, not a lost traceback
            LAST.update(at=datetime.now().astimezone().isoformat(timespec="minutes"), error=f"{type(e).__name__}: {e}")
        finally:
            LOCK.release()

    t = threading.Thread(target=work, name="outlook", daemon=True)
    try:
        t.start()
    except Exception:  # noqa: BLE001 — a thread that never ran will never release the lock
        LOCK.release()
        raise
    return t


# One player at a time, on a click: a free agent from the Waivers page, or a Roster card. Players being
# written (to which store), and the lock that guards that map and each store's read-modify-write (the
# calls run side by side, WORKERS at most).
_WRITING: dict[int, str] = {}
_WLOCK = threading.Lock()


def writing(kind: str = ROSTER) -> set[int]:
    """The players whose one-off write into `kind`'s store is running."""
    return {pid for pid, k in _WRITING.items() if k == kind}


def roster_entry(card: dict, week: int) -> dict:
    """What a Roster card's rewrite polls: his outlook fields as /roster attaches them."""
    a = attach([card], load(week), writing())[0]
    return {k: a[k] for k in ("outlook", "outlook_at", "outlook_stale", "outlook_running", "outlook_error")}


def waiver_entry(row: dict, week: int) -> dict:
    """What the Waivers dialog shows for him: the stored bullets, stale when his facts have moved, whether
    a write is running, and the last write's failure, which a good write clears."""
    e = load(week, WAIVERS).get(row["player_id"])
    e = e if isinstance(e, dict) else {}
    ok = isinstance(e.get("bullets"), list) and "card_hash" in e
    return {"bullets": e["bullets"] if ok else None, "at": e.get("generated_at") if ok else None,
            "stale": ok and e["card_hash"] != card_hash(row), "running": row["player_id"] in _WRITING,
            "error": e.get("error")}


def written(week: int) -> set[int]:
    """The free agents with bullets this week, for the Waivers table's button."""
    return {pid for pid, e in load(week, WAIVERS).items() if isinstance(e, dict) and isinstance(e.get("bullets"), list)}


def start_one(row: dict, week: int, run=run_claude, gather=None, kind: str = WAIVERS) -> threading.Thread:
    """Write one player's outlook on a daemon thread: a free agent (WAIVERS, the claim-framed sheet) or a
    Roster card (ROSTER, stored with the fact hash, so the hourly run skips him until his facts move). It
    always writes: the click is the ask. Busy when he is already being written, WORKERS calls are in
    flight, or — for a card — the full run holds the Roster entries. A failed call keeps the old bullets and
    records the reason on his entry."""
    pid = row["player_id"]
    with _WLOCK:
        if pid in _WRITING:
            raise Busy(f"{row['name']}'s outlook is already being written")
        if len(_WRITING) >= WORKERS:
            raise Busy(f"{WORKERS} outlooks are already being written")
        if kind == ROSTER and LOCK.locked():
            raise Busy("the full outlook run is writing; his card is part of it")
        _WRITING[pid] = kind

    def work():
        try:
            today = date.today()
            ctx = (gather or news.gather)([row], week)[pid]
            bullets = run(fact_sheet(row, week, ctx, today, free_agent=kind == WAIVERS))
            entry = {"card_hash": card_hash(row), "bullets": bullets,
                     "generated_at": datetime.now().astimezone().isoformat(timespec="minutes")}
            if kind == ROSTER:
                entry["hash"] = fact_hash(row, ctx, today)
        except Exception as e:  # noqa: BLE001 — the page needs the reason, not a lost traceback
            entry = {"error": f"{type(e).__name__}: {e}"[:200]}
        with _WLOCK:
            try:
                entries = load(week, kind)
                old = entries.get(pid) if isinstance(entries.get(pid), dict) else {}
                entries[pid] = entry if "bullets" in entry else {**old, **entry}
                put(week, pid, entries[pid], kind)
            finally:
                _WRITING.pop(pid, None)

    t = threading.Thread(target=work, name=f"outlook-{pid}", daemon=True)
    try:
        t.start()
    except Exception:  # noqa: BLE001 — a thread that never ran would hold his slot forever
        with _WLOCK:
            _WRITING.pop(pid, None)
        raise
    return t
