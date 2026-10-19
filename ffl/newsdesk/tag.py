"""Jev tags each player an item names and groups the item into a story.

One call an item: per matched player, `about:<pid>` (is the text about him, not a coach or another
player with his name; under ABOUT he is dropped before anything else), `effect:<pid>` (▲ up / ▼ down /
● none for his fantasy value) and `kind:<pid>`; with stories in the window, `story` (which one this
item reports, or `new`) and `retract` (does it say an earlier report was wrong). An attached item that
adds a material fact (`changed`, a second call) bumps the story's rev, so it is written and pushed
again. A player row's `prob` is the probability of the chosen effect: the push cutoff (Task 8) reads it.

A media item (a transcript excerpt, ext_id `<media id>#<pid>`) is already one player's: he comes from
the suffix, and the excerpt is matched only to add the others it names, all under the same `about`.
An edited item (tagged_at cleared by the collectors' recheck) is tagged again against its own story.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import db
from . import jev
from .jev import JevError
from .players import Index, match

WINDOW = timedelta(days=3)  # a story takes new items while it was updated this recently
ABOUT = 0.5
RETRACT = 0.5
CHANGED = 0.5
MAX_OPTIONS = 254  # Jev takes 255 options a choice; `new` is the last
MAX_TEXT = 60_000  # chars (~15k tokens): inside the 32k per-question context with room for the question
FACT_TEXT = 1500  # chars of each earlier source shown to `changed`
MEDIA = ("yt:", "pod:")
OWN_FAULT = {400, 413, 422}  # this item's request is bad: skip it, the rest still run
KINDS = {"injury": "he is hurt, limited, or listed on an injury report",
         "return": "he is coming back from injury, suspension or a reserve list",
         "role": "his playing time, targets, carries or job changes",
         "depth": "a teammate's move or injury changes the depth chart around him",
         "other": "anything else: contract, off-field, a quote, a recap mention"}
STORY_Q = ("Which earlier story reports the same specific development as this text: the same injury, the same "
           "transaction, the same change in a player's role or status? Sharing a game, a week or a team is not "
           "enough. `new` if none of them does.")
RETRACT_Q = "This text says an earlier report was wrong or has been withdrawn."
CHANGED_Q = ("Does this text report a new fact about the development in `story` that changes it: a new status, "
             "diagnosis, timeline or role, beyond what `story` already says? More detail, quotes or reaction "
             "about the same facts is not a change.")


def _who(p: dict) -> str:
    return f"{p['name']}, the {p['team']} {p['pos']}"


def _state(item: dict) -> dict:
    return {"title": item["title"], "text": (item["text"] or "")[:MAX_TEXT], "club": item["club"]}


def _label(c: dict) -> str:
    title = c.get("headline") or next((i["title"] for i in c.get("items") or []), "")
    return f"{(c.get('first_at') or '')[:10]} {title} — {', '.join(c.get('names') or [])}"


def questions(item: dict, players: list[dict], candidates: list[dict]) -> dict:
    """`players`: {player_id, name, pos, team}. `candidates`: stories (newest kept when over MAX_OPTIONS),
    each dressed with `names`. `new` leads the story options: Jev leans to the first option, and a
    duplicate story is the cheaper mistake than a wrong merge."""
    qs: dict[str, dict] = {}
    for p in players:
        pid, who, name = p["player_id"], _who(p), p["name"]
        qs[f"about:{pid}"] = {"type": "noul", "instructions":
                              f"This text is about {who}, not someone else with that name (a coach, a different player)."}
        qs[f"effect:{pid}"] = {"type": "choice", "instructions":
                               f"What does this news mean for {who}'s fantasy value over the coming weeks?",
                               "criteria": {"up": f"{name} should score more fantasy points or play a bigger role than before",
                                            "down": f"{name} should score fewer fantasy points, miss time, or lose work",
                                            "none": f"no real change to {name}'s fantasy value, or he is only mentioned"}}
        qs[f"kind:{pid}"] = {"type": "choice", "instructions": f"What kind of news is this about {who}?", "criteria": KINDS}
    if candidates:
        newest = sorted(candidates, key=lambda c: (c["updated_at"], c["id"]), reverse=True)[:MAX_OPTIONS]
        qs["story"] = {"type": "choice", "instructions": STORY_Q,
                       "criteria": {"new": "none of these stories; this is news of its own",
                                    **{f"s{c['id']}": _label(c) for c in newest}}}
        qs["retract"] = {"type": "noul", "instructions": RETRACT_Q}
    return qs


def read(answers: dict, players: list[dict]) -> dict:
    """{players: [{player_id, effect, kind, prob, confidence}], story, retract}: players under ABOUT are
    dropped; with none left there is no story."""
    kept = []
    for p in players:
        pid = p["player_id"]
        if answers[f"about:{pid}"]["noul"] < ABOUT:
            continue
        eff = answers[f"effect:{pid}"]
        kept.append({"player_id": pid, "effect": eff["choice"], "kind": answers[f"kind:{pid}"]["choice"],
                     "prob": eff["probabilities"][eff["choice"]], "confidence": eff["confidence"]})
    pick = (answers.get("story") or {}).get("choice")
    story = int(pick[1:]) if kept and pick and pick != "new" else None
    return {"players": kept, "story": story, "retract": (answers.get("retract") or {}).get("noul")}


def _facts(story: dict, item_id: int | None) -> dict:
    """What the story already says: its written words and its other sources."""
    facts = {k: story[k] for k in ("headline", "overview") if story.get(k)}
    others = [{"title": i["title"], "text": (i["text"] or "")[:FACT_TEXT]}
              for i in story.get("items") or [] if i["id"] != item_id]
    if others:
        facts["sources"] = others
    return facts


def changed(story: dict, item: dict, ask=jev.ask) -> bool:
    """Does the item add a material fact to the story? A story with nothing on record yet is unwritten
    anyway, so it counts as changed without a call."""
    facts = _facts(story, item.get("id"))
    if not facts:
        return True
    q = {"changed": {"type": "noul", "instructions": {"story": facts, "question": CHANGED_Q}}}
    return ask(_state(item), q)["changed"]["noul"] >= CHANGED


def _players(item: dict, index: Index) -> list[dict]:
    clubs = (item["club"],) if item["club"] else ()
    pids: list[int] = []
    media = item["source_id"].startswith(MEDIA) and "#" in item["ext_id"]
    if media:
        own = item["ext_id"].rsplit("#", 1)[1]
        if own.isdigit():
            pids.append(int(own))
    text = item["text"] if media else f"{item['title']}\n{item['text']}"
    pids += [p for p in match(text or "", index, clubs) if p not in pids]
    return [{"player_id": p, **index.by_id[p]} for p in pids if p in index.by_id]


def _candidates(pids: list[int], index: Index, now: datetime) -> list[dict]:
    out = db.recent_stories(pids, (now - WINDOW).isoformat())
    for c in out:
        c["names"] = [index.by_id[p["player_id"]]["name"] for p in c["players"] if p["player_id"] in index.by_id]
    return out


def _kind(kept: list[dict]) -> str:
    return next((p["kind"] for p in kept if p["effect"] != "none"), kept[0]["kind"])


def _attach(item: dict, story: dict, kept: list[dict], bump: bool, corrected: bool, at: str) -> None:
    """Rows for every player on a bump or a correction, else only for players new to the story."""
    sid = story["id"]
    if story.get("first_at") is None or item["published_at"] < story["first_at"]:
        db.update_story(sid, {"first_item_id": item["id"], "first_at": item["published_at"]})
    if corrected:
        db.set_story_status(sid, "corrected", at, note=item["source_id"])
    elif bump:
        db.update_story(sid, {"rev": story["rev"] + 1, "updated_at": at})
    on = {p["player_id"] for p in story["players"]}
    db.put_story_players(sid, [p for p in kept if bump or corrected or p["player_id"] not in on])


def _tag(item: dict, index: Index, ask, now: datetime, out: dict) -> None:
    at = now.isoformat()
    players = _players(item, index)
    if not players:
        db.tag_item(item["id"], [], None, None, at)
        out["tagged"] += 1
        return
    own = db.story(item["story_id"]) if item["story_id"] is not None else None
    cands = [] if own else _candidates([p["player_id"] for p in players], index, now)
    answers = ask(_state(item), questions(item, players, cands))
    got = read(answers, players)
    kept = got["players"]
    pids = [p["player_id"] for p in kept]
    if not kept:
        db.tag_item(item["id"], [], answers, None, at)
        out["tagged"] += 1
        return
    if own:  # an edit: its own story only, bumped when the new text says something new
        bump = changed(own, item, ask)
        _attach(item, own, kept, bump, False, at)
        db.tag_item(item["id"], pids, answers, own["id"], at)
        out["tagged"] += 1
        out["updated"] += bump
        return
    chosen = next((c for c in cands if c["id"] == got["story"]), None)
    if chosen and not {p["player_id"] for p in chosen["players"]} & set(pids):
        chosen = None  # its only link to this item was a player the item turned out not to be about
    if chosen is None:
        sid = db.add_story({"first_item_id": item["id"], "first_at": item["published_at"], "updated_at": at,
                            "kind": _kind(kept)})
        db.put_story_players(sid, kept)
        db.tag_item(item["id"], pids, answers, sid, at)
        out["tagged"] += 1
        out["new"] += 1
        return
    corrected = (got["retract"] or 0) >= RETRACT
    bump = not corrected and changed(chosen, item, ask)
    _attach(item, chosen, kept, bump, corrected, at)
    db.tag_item(item["id"], pids, answers, chosen["id"], at)
    out["tagged"] += 1
    out["attached"] += 1
    out["updated"] += bump or corrected


def run(index: Index, ask=jev.ask, now: datetime | None = None) -> dict:
    """Tag every untagged item, oldest first. An item whose request Jev refuses (OWN_FAULT) is skipped and
    left untagged; any other Jev failure, or a DB error, stops the run (`error`), the rest wait for the next."""
    now = now or datetime.now(timezone.utc)
    out = {"tagged": 0, "new": 0, "attached": 0, "updated": 0, "failed": [], "error": None}
    try:
        for item in db.untagged_items():
            try:
                _tag(item, index, ask, now, out)
            except JevError as e:
                if e.status in OWN_FAULT:
                    out["failed"].append(item["id"])
                    continue
                out["error"] = str(e)
                break
    except db.DBError as e:
        out["error"] = f"db: {e}"
    return out
