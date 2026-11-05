"""News pushes, pure: which written stories reach the phone, and what they say.

A player row pushes when it moves my roster down (▼ on a player I hold), clears one of mine for the game (▲ with `ready`: a
tagged player now active, for the lineup call), or moves a free agent who matters up (▲ on one at MIN_OWNED percent owned,
or first on his club's chart: a no-name off IR is no news); and the tagger is sure of it (NEWS_PUSH_PROB), Haiku wrote him a
line, `check.py` reached the same arrow (`flag`), and he is not muted. A kicker or defense
(`pos`, off the player index) pushes only as a ▼ on my roster that is an injury or his club letting him go (QUIET_POS:
The tagger has no kind for a release, so it is read off Haiku's line, RELEASED). One push per story
revision (`news:<id>:<rev>`); a correction or retraction pushes once more (`news:<id>:<status>`), only when the
story pushed before. At most one push per player in DEDUPE, across the user's leagues: `recent` is what was queued in
that time (sent or waiting), off the notices table and the stories' rows; only a fresh drop to out or IR or being cleared
for the game (`urgent`) gets past it, so a practice week's steps are one push. Only news still new pushes: the story's newest source (a status push: its
status change) at most NEWS_PUSH_MAX_AGE old, so the first run's three days of items, or a catch-up after a stretch at
Haiku's limit, stays in the feed without pushing.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..notify import BODY_MAX
from .rules import GONE

NEWS_PUSH_PROB = 0.8
NEWS_PUSH_MAX_AGE = timedelta(hours=6)
DEDUPE = timedelta(hours=48)
MIN_OWNED = 5.0  # percent: under it ESPN's players are mostly depth; 1% still let through what no one rosters
ARROW = {"down": "▼", "up": "▲"}
QUIET_POS = {"K", "D/ST"}  # no free-agent pushes; mine only for an injury or a release
RELEASED = re.compile(r"\b(releas|waiv|cut)", re.I)
SEP = " · "
STATUS_TITLE = {"corrected": "Corrected", "retracted": "Retracted"}
STATUS_BODY = {"corrected": "The report was corrected.", "retracted": "Every source withdrew the report."}


def matters(p: dict) -> bool:
    """A player worth a free-agent push. `owned` unknown (no reading) counts as worth it."""
    return p.get("owned") is None or p["owned"] >= MIN_OWNED or bool(p.get("starter"))


def _pushes(p: dict, owners: dict[int, dict], me: int, muted: set[int], recent: set[int]) -> bool:
    pid = p["player_id"]
    if p["effect"] == "down":
        ours = (owners.get(pid) or {}).get("team_id") == me
    elif p["effect"] == "up":
        mine = (owners.get(pid) or {}).get("team_id") == me
        ours = (mine and bool(p.get("ready"))) or (pid not in owners and matters(p))
    else:
        return False
    if p.get("pos") in QUIET_POS and not (p["effect"] == "down" and (
            p["kind"] == "injury" or RELEASED.search(p.get("reason") or ""))):
        return False
    return (ours and p["prob"] >= NEWS_PUSH_PROB and not p.get("flag") and bool((p.get("reason") or "").strip())
            and pid not in muted and pid not in recent)


def _qualifying(story: dict, owners: dict[int, dict], me: int, muted: set[int], recent: set[int]) -> list[dict]:
    """The rows that push, ▼ first, then by prob; none for a story Haiku gave no headline."""
    if not (story.get("headline") or "").strip():
        return []
    rows = [p for p in story["players"] if _pushes(p, owners, me, muted, recent)]
    return sorted(rows, key=lambda p: (p["effect"] != "down", -p["prob"]))


def rows(story: dict, owners: dict[int, dict], me: int, muted: set[int], recent: set[int] = frozenset()) -> list[int]:
    """The players the push carries: what the dedupe counts after it."""
    return [p["player_id"] for p in _qualifying(story, owners, me, muted, recent)]


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


def _new(when: str | None, at: str) -> bool:
    """Within NEWS_PUSH_MAX_AGE of `at`; compared as times, since `at` is local and stored times are UTC."""
    return bool(when) and datetime.fromisoformat(at) - datetime.fromisoformat(when) <= NEWS_PUSH_MAX_AGE


def for_story(story: dict, owners: dict[int, dict], me: int, muted: set[int], at: str,
              recent: set[int] = frozenset()) -> dict | None:
    """The push for a written story revision, or None when no row qualifies or its newest source is too old to be
    news. A row's name is `name` (the pipeline dresses the rows from the player index)."""
    if not _new(max((i["published_at"] for i in story.get("items") or []), default=None), at):
        return None
    got = _qualifying(story, owners, me, muted, recent)
    if not got:
        return None
    lines = [f"{ARROW[p['effect']]} {p.get('name') or 'Player %d' % p['player_id']}: {p['reason'].strip()}" for p in got]
    return {"key": f"news:{story['id']}:{story['rev']}", "kind": "news", "title": story["headline"], "body": body(lines),
            "url": f"/news?story={story['id']}", "created_at": at}


def for_status(story: dict, pushed: list[str], at: str, sources: dict[str, dict] | None = None) -> dict | None:
    """A corrected or retracted story's one push, only when an earlier `news:<id>:*` notice was sent, Haiku gave it a
    headline and the status changed within NEWS_PUSH_MAX_AGE. The body names the source behind it (`sources`: id ->
    news_sources row, as feed.shape names it), its id when unknown."""
    status, sid = story["status"], story["id"]
    if (status not in STATUS_TITLE or not (story.get("headline") or "").strip()
            or not any(k.startswith(f"news:{sid}:") for k in pushed) or not _new(story.get("status_at"), at)):
        return None
    note = story.get("status_note")
    return {"key": f"news:{sid}:{status}", "kind": "news", "title": f"{STATUS_TITLE[status]}: {story['headline']}",
            "body": ((sources or {}).get(note) or {}).get("name") or note or STATUS_BODY[status], "url": f"/news?story={sid}",
            "created_at": at}


def rev_key(key: str) -> tuple[int, int] | None:
    """(story id, rev) of a `news:<id>:<rev>` key; None for a status key."""
    parts = key.split(":")
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    return None


def recent(queued: list[dict], stories: dict[int, dict], t: datetime) -> set[int]:
    """Every player whose row could have pushed (▲/▼, at NEWS_PUSH_PROB, a line, unflagged) on a story pushed (sent, or
    queued and waiting) in the last DEDUPE. Owners decided which of them went out and are not kept, so this may hold a
    little more than the push carried."""
    out: set[int] = set()
    for n in queued:
        if n["outcome"] not in (None, "sent") or t - datetime.fromisoformat(n["created_at"]) > DEDUPE:
            continue
        sr = rev_key(n["key"])
        s = stories.get(sr[0]) if sr else None
        for p in (s or {}).get("players", []):
            if (p["effect"] in ARROW and p["prob"] >= NEWS_PUSH_PROB and not p.get("flag")
                    and (p.get("reason") or "").strip()):
                out.add(p["player_id"])
    return out


def _newest(story: dict) -> dict:
    """The answers of the story's newest live source."""
    items = [i for i in story.get("items") or [] if not i.get("withdrawn_at")]
    return (max(items, key=lambda i: (i["published_at"], i["id"])).get("jev") or {}) if items else {}


def ready(story: dict) -> set[int]:
    """The players its newest source clears for the game (`rules.ready`)."""
    jev = _newest(story)
    return {p["player_id"] for p in story["players"] if p["effect"] == "up" and f"ready:{p['player_id']}" in jev}


def urgent(story: dict) -> set[int]:
    """The players past the dedupe: its newest source just dropped them to out or IR (rules.GONE, a fresh step), or
    cleared them for the game."""
    jev = _newest(story)
    return ready(story) | {p["player_id"] for p in story["players"] if p["effect"] == "down"
                           and (jev.get(f"status:{p['player_id']}") or {}).get("status") in GONE
                           and (jev.get(f"fresh:{p['player_id']}") or {}).get("noul")}
