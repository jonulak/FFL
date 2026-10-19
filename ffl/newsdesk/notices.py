"""News pushes, pure: which written stories reach the phone, and what they say.

A player row pushes when it moves my roster down (▼ on a player I hold) or a free agent up (▲), Jev is sure of it
(NEWS_PUSH_PROB), Haiku wrote him a line and did not disagree with the arrow, and he is not muted. One push per story
revision (`news:<id>:<rev>`); a correction or retraction pushes once more (`news:<id>:<status>`), only when the
story pushed before. At most one push per player and kind of news in DEDUPE: `recent` is what was queued in that
time (sent or waiting), off the notices table and the stories' rows; a story whose earlier revision pushed is a
material update (`is_update`) and skips it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..notify import BODY_MAX

NEWS_PUSH_PROB = 0.8
DEDUPE = timedelta(hours=24)
ARROW = {"down": "▼", "up": "▲"}
SEP = " · "
STATUS_TITLE = {"corrected": "Corrected", "retracted": "Retracted"}
STATUS_BODY = {"corrected": "The report was corrected.", "retracted": "Every source withdrew the report."}


def _pushes(p: dict, owners: dict[int, dict], me: int, muted: set[int], recent: set[tuple[int, str]]) -> bool:
    pid = p["player_id"]
    if p["effect"] == "down":
        ours = (owners.get(pid) or {}).get("team_id") == me
    elif p["effect"] == "up":
        ours = pid not in owners
    else:
        return False
    return (ours and p["prob"] >= NEWS_PUSH_PROB and not p.get("flag") and bool((p.get("reason") or "").strip())
            and pid not in muted and (pid, p["kind"]) not in recent)


def _qualifying(story: dict, owners: dict[int, dict], me: int, muted: set[int],
                recent: set[tuple[int, str]]) -> list[dict]:
    """The rows that push, ▼ first, then by prob; none for a story Haiku gave no headline."""
    if not (story.get("headline") or "").strip():
        return []
    rows = [p for p in story["players"] if _pushes(p, owners, me, muted, recent)]
    return sorted(rows, key=lambda p: (p["effect"] != "down", -p["prob"]))


def rows(story: dict, owners: dict[int, dict], me: int, muted: set[int],
         recent: set[tuple[int, str]] = frozenset()) -> list[tuple[int, str]]:
    """(player, kind) of the rows the push carries: what the day's dedupe counts after it."""
    return [(p["player_id"], p["kind"]) for p in _qualifying(story, owners, me, muted, recent)]


def _more(n: int) -> str:
    return f" +{n} more" if n else ""


def body(lines: list[str]) -> str:
    """Whole lines while they fit BODY_MAX with the ' +k more' still owed, so the flush's cap never cuts one."""
    kept: list[str] = []
    for i, line in enumerate(lines):
        if len(SEP.join(kept + [line]) + _more(len(lines) - i - 1)) > BODY_MAX:
            break
        kept.append(line)
    if not kept:  # the first line alone is too long (Haiku's lines are 140 at most, so not in practice)
        more = _more(len(lines) - 1)
        return lines[0][:BODY_MAX - len(more) - 1] + "…" + more
    return SEP.join(kept) + _more(len(lines) - len(kept))


def for_story(story: dict, owners: dict[int, dict], me: int, muted: set[int], at: str,
              recent: set[tuple[int, str]] = frozenset()) -> dict | None:
    """The push for a written story revision, or None when no row qualifies. A row's name is `name` (the pipeline
    dresses the rows from the player index)."""
    got = _qualifying(story, owners, me, muted, recent)
    if not got:
        return None
    lines = [f"{ARROW[p['effect']]} {p.get('name') or 'Player %d' % p['player_id']}: {p['reason'].strip()}" for p in got]
    return {"key": f"news:{story['id']}:{story['rev']}", "kind": "news", "title": story["headline"], "body": body(lines),
            "url": f"/news?story={story['id']}", "created_at": at}


def for_status(story: dict, pushed: list[str], at: str) -> dict | None:
    """A corrected or retracted story's one push, only when an earlier `news:<id>:*` notice was sent and Haiku gave it
    a headline."""
    status, sid = story["status"], story["id"]
    if (status not in STATUS_TITLE or not (story.get("headline") or "").strip()
            or not any(k.startswith(f"news:{sid}:") for k in pushed)):
        return None
    return {"key": f"news:{sid}:{status}", "kind": "news", "title": f"{STATUS_TITLE[status]}: {story['headline']}",
            "body": story.get("status_note") or STATUS_BODY[status], "url": f"/news?story={sid}", "created_at": at}


def rev_key(key: str) -> tuple[int, int] | None:
    """(story id, rev) of a `news:<id>:<rev>` key; None for a status key."""
    parts = key.split(":")
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    return None


def recent(queued: list[dict], stories: dict[int, dict], t: datetime) -> set[tuple[int, str]]:
    """(player, kind) of every row that could have pushed (▲/▼, at NEWS_PUSH_PROB, a line, unflagged) on a story pushed
    (sent, or queued and waiting) in the last DEDUPE. Owners decided which of them went out and are not kept, so this
    may hold a little more than the push carried."""
    out: set[tuple[int, str]] = set()
    for n in queued:
        if n["outcome"] not in (None, "sent") or t - datetime.fromisoformat(n["created_at"]) > DEDUPE:
            continue
        sr = rev_key(n["key"])
        s = stories.get(sr[0]) if sr else None
        for p in (s or {}).get("players", []):
            if (p["effect"] in ARROW and p["prob"] >= NEWS_PUSH_PROB and not p.get("flag")
                    and (p.get("reason") or "").strip()):
                out.add((p["player_id"], p["kind"]))
    return out


def is_update(story: dict, pushed: list[str]) -> bool:
    """A material update: an earlier revision of this story was pushed."""
    return any((sr := rev_key(k)) and sr[0] == story["id"] and sr[1] < story["rev"] for k in pushed)
