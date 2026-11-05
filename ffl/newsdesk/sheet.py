"""Which of a player's stories an outlook's fact sheet reads. A busy player has over a hundred in its window and most only
name him, so the newest few hid his own news (Lamar Jackson's IR story was his 27th).

Written up, KEEP at most: his ARROWS newest arrow stories, then text about him, then an excerpt cut for him that adds to
what is kept; older arrows take a slot left over. A story that only names him gives at most a line: the sentences naming
him, from text only, since a transcript's are too garbled to read alone. Replayed on the Nov 1-4 dev run, this kept 150 of
170 arrow stories and all 94 players' newest, against 63 and 54 for the newest six, with a quarter more news text and a
third fewer changes to the stories that decide a rewrite.
"""
from __future__ import annotations

from . import rules

KEEP = 8
ARROWS = 3  # past this, old arrows would crowd out what is current: "back at practice" while he is on IR
LINES = 4
LINE_CHARS = 200
LINE_WORDS = 6  # a clause shorter than this says nothing alone: "Tucker Kraft (12 yards)"


def _media(item: dict) -> bool:
    return item["source_id"].startswith(rules.MEDIA)


def _about(story: dict, items: list[dict], pid: int, me) -> bool:
    """Text about him: the written headline names him (the desk's call, unlike a post's long title that drops his name in
    passing), or the title when it isn't written yet, or it is his own Rotowire note, which often calls him only "he"."""
    text = [i for i in items if not _media(i)]
    if any(i["source_id"] == "rotowire" and i["url"].endswith(f"/{pid}") for i in text):
        return True
    return bool(text) and (me.search(story["headline"]) if story["headline"] else any(me.search(i["title"]) for i in text))


def _words(story: dict, items: list[dict]) -> str:
    return f"{story['headline'] or items[0]['title']} {story['overview'] or items[0]['text'][:rules.LEAD_CHARS]}"


def _lines(rest: list[tuple[dict, list[dict]]], pid: int, name: str) -> list[dict]:
    player = {"player_id": pid, "name": name}
    out: list[dict] = []
    seen: set[str] = set()
    for story, items in rest:
        for item in (i for i in items if not _media(i)):
            clauses = [c for c in dict.fromkeys(rules.scope(item, player, [])) if len(c.split()) >= LINE_WORDS]
            if clauses:
                text = " ".join(" ".join(clauses).split())[:LINE_CHARS]
                if text not in seen:
                    seen.add(text)
                    out.append({"id": story["id"], "text": text, "item": item})
                break
        if len(out) == LINES:
            break
    return out


def choose(stories: list[dict], pid: int, name: str) -> tuple[list[dict], list[dict]]:
    """From db.player_stories' rows (newest first): the stories to write up, still newest first, and the lines,
    each {id, text, item}, of the ones that only name him. Withdrawn sources count for nothing."""
    me = rules._named(name)
    arrows, text, excerpts, rest = [], [], [], []
    live: dict[int, list[dict]] = {}
    for s in stories:
        items = live[s["id"]] = [i for i in s["items"] if not i.get("withdrawn_at")]
        if not items:
            continue
        row = next((r for r in s["players"] if r["player_id"] == pid), {})
        if row.get("effect") not in (None, "none"):
            arrows.append(s)
        elif _about(s, items, pid, me):
            text.append(s)
        elif any(_media(i) and i["ext_id"].endswith(f"#{pid}") for i in items):
            excerpts.append((s, items))
        else:
            rest.append((s, items))
    kept = arrows[:ARROWS]
    kept += text[:KEEP - len(kept)]
    for s, items in excerpts:
        if len(kept) == KEEP:
            break
        if all(rules.similarity(_words(s, items), _words(k, live[k["id"]])) < rules.SIMILAR for k in kept):
            kept.append(s)
    kept += arrows[ARROWS:][:KEEP - len(kept)]
    ids = {s["id"] for s in kept}
    return [s for s in stories if s["id"] in ids], _lines(rest, pid, name)
