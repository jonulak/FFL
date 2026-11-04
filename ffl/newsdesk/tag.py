"""The tagger (`rules.py`, no model) tags each player an item names and groups the item into a story.

Per matched player: `about:<pid>` (the name is his, not a coach's or another player's; if not he is dropped before
anything else), `subject:<pid>` (the item reports on him, not a passing mention; if not he is dropped too), `effect:<pid>`
(▲ up / ▼ down / ● none, the step on his status ladder), `fresh:<pid>` (his status moved; if not his effect is ●) and
`kind:<pid>`; with stories in the window, `story` (which one it reads like, or `new`) and `retract` (does it say an earlier
report was wrong). The story pick is a proposal: the item joins only through a shared player with the same kind of news
(`_joins`; role and depth count as one, `_same_kind`). Failing that, a player the item moves (▲/▼) threads it into his
newest story of the same kind updated within THREAD_HOURS, one he leads (`_lead`: its founding item's surest mover) and is
▲/▼ on (`_thread`): doubtful, out and IR are steps of one situation, not three stories. Otherwise it starts its own story.
An attached item that moves a player's status bumps the story's rev, so it is written and pushed again. A player row's
`prob` is the rule's: the push cutoff reads it.

A media item (a transcript excerpt, ext_id `<media id>#<pid>`) is already one player's: he comes from the suffix, and the
excerpt is matched only to add the others it names, all under the same `about`. An edited item (tagged_at cleared by the
collectors' recheck) is tagged again against its own story; one that is now about none of its players is withdrawn from
it, so a story it was the last source of is retracted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import db
from . import rules
from .players import Index, match

WINDOW = timedelta(days=3)  # a story takes new items while it was updated this recently
ABOUT = 0.5
SUBJECT = 0.5  # under it he is only named in passing (a roundup, recap, preview): dropped from the story as under ABOUT
RETRACT = 0.5
FRESH = 0.7  # under it the item only repeats what is known about him: his row is ● whatever `effect` said
STORY_ATTACH_PROB = 0.6  # the story pick is a proposal: below this the item starts its own story
THREAD_HOURS = 48  # a player's ▲/▼ news joins his story of the same kind updated this recently, whatever was picked
MEDIA = ("yt:", "pod:")


def read(answers: dict, players: list[dict]) -> dict:
    """{players: [{player_id, effect, kind, prob, confidence}], story, retract}: players under ABOUT or SUBJECT
    are dropped; with none left there is no story. Answers from before `subject` count as a subject."""
    kept = []
    for p in players:
        pid = p["player_id"]
        if (answers[f"about:{pid}"]["noul"] < ABOUT
                or (answers.get(f"subject:{pid}") or {}).get("noul", 1.0) < SUBJECT):
            continue
        eff = answers[f"effect:{pid}"]
        # a passing mention of news already known moves nobody; answers from before `fresh` count as fresh
        effect = eff["choice"] if (answers.get(f"fresh:{pid}") or {}).get("noul", 1.0) >= FRESH else "none"
        kept.append({"player_id": pid, "effect": effect, "kind": answers[f"kind:{pid}"]["choice"],
                     "prob": eff["probabilities"].get(effect, 0.0), "confidence": eff["confidence"]})
    ans = answers.get("story") or {}
    pick = ans.get("choice")
    story = int(pick[1:]) if kept and pick and pick != "new" else None
    return {"players": kept, "story": story, "story_prob": (ans.get("probabilities") or {}).get(pick) if story else None,
            "retract": (answers.get("retract") or {}).get("noul")}


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


def _candidates(pids: list[int], now: datetime) -> list[dict]:
    return db.recent_stories(pids, (now - WINDOW).isoformat())


def _lifted(answers: dict) -> list[dict]:
    """The players the depth chart added: on the story, but not among those the item names (`player_ids`)."""
    return [{"player_id": int(k.split(":")[1])} for k in answers if k.startswith("chart:")]


def _kind(kept: list[dict]) -> str:
    return next((p["kind"] for p in kept if p["effect"] != "none"), kept[0]["kind"])


# Kinds that are one kind of news for grouping: a signing or a release is "role" to one source and "depth" to the
# next (the tagger split Dalton's move to Atlanta 71% role / 48% depth, 2026-10-19, and it became five stories).
KIND_FAMILY = {"depth": "role"}


def _same_kind(a: str | None, b: str | None) -> bool:
    return a is not None and KIND_FAMILY.get(a, a) == KIND_FAMILY.get(b, b)


def _joins(story: dict, kept: list[dict], prob: float | None) -> bool:
    """The tagger's story pick is a proposal; it stands only when it is sure enough and the story shares a player
    the item is about, with the same kind of news for him. An injury report never sinks into a game recap:
    a merged story hides the news, a duplicate does not."""
    on = {p["player_id"]: p["kind"] for p in story["players"]}
    return (prob or 0) >= STORY_ATTACH_PROB and any(_same_kind(on.get(p["player_id"]), p["kind"]) for p in kept)


def _lead(story: dict) -> int | None:
    """Whose situation the story is: the surest ▲/▼ player (effect prob; a tie goes to the first named) of the item
    that founded it, the first tagged into it (a run tags newest first). A story with no tagged item falls back to
    its surest ▲/▼ row."""
    tagged = sorted((i for i in story.get("items") or [] if i.get("tagged_at") and i.get("jev")),
                    key=lambda i: (i["published_at"], i["id"]), reverse=True)
    if tagged:
        founder = min(tagged, key=lambda i: i["tagged_at"])
        rows = read(founder["jev"], [{"player_id": p} for p in founder["player_ids"] or []])["players"]
    else:
        rows = story["players"]
    moved = [r for r in rows if r["effect"] != "none"]
    return max(moved, key=lambda r: r["prob"])["player_id"] if moved else None


def _thread(cands: list[dict], kept: list[dict], now: datetime, answers: dict | None = None) -> tuple[dict, list[dict]] | None:
    """The story of a player's developing situation: one he leads (`_lead`), ▲/▼ on it with the same kind of news.
    Only his row is written, so a roundup's other players never land on someone else's story; and a roundup's other
    movers do not pull their own news into the roundup's story. With stories for several of its players, the item
    goes to the one of the player it is surest of (effect prob)."""
    since = (now - timedelta(hours=THREAD_HOURS)).isoformat()

    def his(c: dict, p: dict) -> bool:
        return (c["updated_at"] >= since and _lead(c) == p["player_id"]
                and any(r["player_id"] == p["player_id"] and r["effect"] != "none" and _same_kind(r["kind"], p["kind"])
                        for r in c["players"]))

    for p in sorted((p for p in kept if p["effect"] != "none"), key=lambda p: p["prob"], reverse=True):
        story = next((c for c in cands if his(c, p)), None)  # cands: newest first
        if story:
            behind = [q for q in kept if (answers or {}).get(f"chart:{q['player_id']}", {}).get("behind") == p["player_id"]]
            return story, [p, *behind]  # his next man moves with his step
    return None


def _attach(item: dict, story: dict, kept: list[dict], bump: bool, corrected: bool, at: str) -> bool:
    """Rows for every player on a bump or a correction, else only for players new to the story. A new player
    with ▲/▼ is news of its own: the story is bumped too, so Haiku writes his line and it may push. On a bump a ●
    never overwrites his ▲/▼: a recap mentioning him leaves the story his, for his next step to thread into. Returns
    whether the rev moved."""
    sid = story["id"]
    if story.get("first_at") is None or item["published_at"] < story["first_at"]:
        db.update_story(sid, {"first_item_id": item["id"], "first_at": item["published_at"]})
    on = {p["player_id"]: p["effect"] for p in story["players"]}
    rows = [p for p in kept if corrected or p["player_id"] not in on
            or (bump and (p["effect"] != "none" or on[p["player_id"]] == "none"))]
    if corrected:
        db.set_story_status(sid, "corrected", at, note=item["source_id"])
    elif bump or any(p["effect"] != "none" for p in rows):
        db.update_story(sid, {"rev": story["rev"] + 1, "updated_at": at})
        bump = True
    db.put_story_players(sid, rows)
    return bump or corrected


def _untag(item: dict, answers: dict | None, at: str) -> None:
    """An item about none of the players: no story. An edit that was in one leaves it as a withdrawn source does,
    so the story is retracted when no live source is left."""
    db.tag_item(item["id"], [], answers, item["story_id"], at)
    if item["story_id"] is not None:
        db.withdraw_item(item["id"], at)


def _moved(kept: list[dict]) -> bool:
    """An item adds a material fact when it moves someone: `rules.arrow` gives an arrow only for a step from his last status."""
    return any(p["effect"] != "none" for p in kept)


def _tag(item: dict, index: Index, answer, now: datetime, out: dict) -> None:
    at = now.isoformat()
    players = _players(item, index)
    if not players:
        _untag(item, None, at)
        out["tagged"] += 1
        return
    own = db.story(item["story_id"]) if item["story_id"] is not None else None
    cands = [own] if own else _candidates([p["player_id"] for p in players], now)
    answers = answer(item, players, cands, index=index, now=now)
    lifted = _lifted(answers)
    got = read(answers, players + lifted)
    kept = got["players"]
    off = {p["player_id"] for p in lifted}
    pids = [p["player_id"] for p in kept if p["player_id"] not in off]
    if not pids:
        _untag(item, answers, at)
        out["tagged"] += 1
        return
    if own:  # an edit: its own story only, bumped when the new text moves someone
        bumped = _attach(item, own, kept, _moved(kept), False, at)
        db.tag_item(item["id"], pids, answers, own["id"], at)
        out["tagged"] += 1
        out["updated"] += bumped
        return
    chosen = next((c for c in cands if c["id"] == got["story"]), None)
    if chosen and not _joins(chosen, kept, got["story_prob"]):
        chosen = None
    rows, threaded = kept, False
    if chosen is None and (thread := _thread(cands, kept, now, answers)):
        (chosen, rows), threaded = thread, True
    if chosen is None:
        sid = db.add_story({"first_item_id": item["id"], "first_at": item["published_at"], "updated_at": at,
                            "kind": _kind(kept)})
        db.put_story_players(sid, kept)
        db.tag_item(item["id"], pids, answers, sid, at)
        out["tagged"] += 1
        out["new"] += 1
        return
    # Items are tagged newest first, so a backlog's older item can join a story its newer news already made: it is
    # a source, never a change or a correction of what came after it.
    older = any(i["published_at"] > item["published_at"] for i in chosen["items"] if not i.get("withdrawn_at"))
    # a retraction names the report it takes back: a threaded story was not picked, so it cannot be corrected
    corrected = not older and not threaded and (got["retract"] or 0) >= RETRACT
    change = _moved(rows) if not older and not corrected else False
    bumped = _attach(item, chosen, rows, change, corrected, at)
    db.tag_item(item["id"], pids, answers, chosen["id"], at)
    out["tagged"] += 1
    out["attached"] += 1
    out["updated"] += bumped


def run(index: Index, answer=rules.answer, now: datetime | None = None) -> dict:
    """Tag every untagged item, newest first. An item that raises is skipped, left untagged and listed in `failed`, for
    the next run; a DB error stops the run (`error`)."""
    now = now or datetime.now(timezone.utc)
    out = {"tagged": 0, "new": 0, "attached": 0, "updated": 0, "failed": [], "error": None}
    try:
        for item in db.untagged_items():
            try:
                _tag(item, index, answer, now, out)
            except db.DBError:
                raise
            except Exception:  # noqa: BLE001 — one odd item never holds up the news behind it
                out["failed"].append(item["id"])
    except db.DBError as e:
        out["error"] = f"db: {e}"
    return out
