"""Jev tags each player an item names and groups the item into a story.

One call an item: per matched player, `about:<pid>` (is the text about him, not a coach or another
player with his name; under ABOUT he is dropped before anything else), `subject:<pid>` (the text reports on him,
not a passing mention in a list, recap, preview or roundup; under SUBJECT he is dropped too), `effect:<pid>` (▲ up / ▼ down /
● none for his fantasy value), `fresh:<pid>` (new information about him, not a passing mention of news
already known; under FRESH his effect is forced to ●, so a preview naming an old injury never pushes)
and `kind:<pid>`; with stories in the window, `story` (which one this
item reports, or `new`) and `retract` (does it say an earlier report was wrong). The story pick is a
proposal: the item joins only at STORY_ATTACH_PROB, through a shared player with the same kind of news
(`_joins`; role and depth count as one, `_same_kind`). Failing that, a player the item moves (▲/▼) threads it into his newest story of the same kind
updated within THREAD_HOURS, one he leads (`_lead`: its founding item's surest mover) and is ▲/▼ on (`_thread`):
doubtful, out and IR are steps of one situation, not three stories. Otherwise it starts its own story. An attached item that
adds a material fact (`changed`, a second call) bumps the story's rev, so it is written and pushed
again. The same call asks, of a story with written words, whether its headline still sums it up (`headline`): under
HEADLINE_OK the story is rewritten at the same rev, so the headline follows the news without a push. A player row's
`prob` is the probability of the chosen effect: the push cutoff (Task 8) reads it.

A media item (a transcript excerpt, ext_id `<media id>#<pid>`) is already one player's: he comes from
the suffix, and the excerpt is matched only to add the others it names, all under the same `about`.
An edited item (tagged_at cleared by the collectors' recheck) is tagged again against its own story; one that is
now about none of its players is withdrawn from it, so a story it was the last source of is retracted.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import db
from . import jev, usage
from .jev import JevError
from .players import Index, match

WINDOW = timedelta(days=3)  # a story takes new items while it was updated this recently
ABOUT = 0.5
# under SUBJECT he is only named in passing (a roundup, recap, preview): dropped from the story as under ABOUT. Bench
# 2026-10-17..20 (5,626 readings): 79% under 0.3, 10% over 0.8, 0.4-0.6 the trough; Mayfield in a Bucs recap 0.05
# (about 0.51), Freiermuth in his own calf note 0.95. It cut item rows from 4,645 to 811 and stories from 1,240 to 570
SUBJECT = 0.5
RETRACT = 0.5
CHANGED = 0.5
HEADLINE_OK = 0.5  # under it the written headline no longer sums the story up: rewritten, not pushed
# under FRESH the item only mentions known news about him: his row is ● whatever `effect` said. Set from Hurts
# week (2026-10-19): the IR and blood-clot reports read 0.73-0.93; previews, recaps and the inactives list
# repeating them read 0.55-0.65
FRESH = 0.7
STORY_ATTACH_PROB = 0.6  # Jev's story pick is a proposal: below this the item starts its own story
THREAD_HOURS = 48  # a player's ▲/▼ news joins his story of the same kind updated this recently, whatever Jev picked
MAX_OPTIONS = 254  # Jev takes 255 options a choice; plus `new`
MAX_TEXT = 60_000  # chars (~15k tokens): inside the 32k per-question context with room for the question
FACT_TEXT = 1500  # chars of each earlier source shown to `changed`
MEDIA = ("yt:", "pod:")
TAG_MAX_TRIES = 5  # an item failing this many runs is tagged with no story: it must not head the queue for good
TRIES = "news_tag_tries"  # state: {item id: failed runs}
KINDS = {"injury": "he is hurt, limited, or listed on an injury report",
         "return": "he is coming back from injury, suspension or a reserve list",
         "role": "his playing time, targets, carries or job changes",
         "depth": "a teammate's move or injury changes the depth chart around him",
         "other": "anything else: contract, off-field, a quote, a recap mention"}
# Kept as "the same specific development": asked about "the same ongoing development" (2026-10-19), Jev let game
# recaps, previews and podcast excerpts into a player's story (a 31-item Hurts story); `_thread` joins the steps instead
STORY_Q = ("Which earlier story reports the same specific development as this text: the same injury, the same "
           "transaction, the same change in a player's role or status? Sharing a game, a week or a team is not "
           "enough. `new` if none of them does.")
RETRACT_Q = "This text says an earlier report was wrong or has been withdrawn."
CHANGED_Q = ("Does this text report a new fact about the development in `story` that changes it: a new status, "
             "diagnosis, timeline or role, beyond what `story` already says? More detail, quotes or reaction "
             "about the same facts is not a change.")
HEADLINE_Q = ("Is this headline still an accurate summary of the story in `story` once this new text is included: "
              "{headline!r}? A headline that gives an earlier step (doubtful when he is now out or on injured reserve) "
              "is not.")


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
        qs[f"subject:{pid}"] = {"type": "noul", "instructions":
                                f"This text is about {who} as a subject: it reports on him, his status, role or "
                                "situation; not a passing mention in a list, recap, preview or roundup."}
        qs[f"fresh:{pid}"] = {"type": "noul", "instructions":
                              f"This text reports new information about {name} (a new injury, status, role, transaction "
                              "or return date), not a passing reference to news already known (a recap, preview or "
                              "roundup mentioning it in passing)."}
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


def _facts(story: dict, item_id: int | None) -> dict:
    """What the story already says: its written words and its other sources."""
    facts = {k: story[k] for k in ("headline", "overview") if story.get(k)}
    others = [{"title": i["title"], "text": (i["text"] or "")[:FACT_TEXT]}
              for i in story.get("items") or [] if i["id"] != item_id]
    if others:
        facts["sources"] = others
    return facts


def weigh(story: dict, item: dict, ask=jev.ask) -> tuple[bool, bool]:
    """(Does the item add a material fact to the story, is the story's written headline stale with it?) A story with
    nothing on record yet is unwritten anyway, so it counts as changed without a call; one whose words Haiku has not
    written has no headline to judge."""
    facts = _facts(story, item.get("id"))
    if not facts:
        return True, False
    q = {"changed": {"type": "noul", "instructions": {"story": facts, "question": CHANGED_Q}}}
    if story.get("written_rev") and story.get("headline"):
        q["headline"] = {"type": "noul", "instructions": {
            "story": facts, "question": HEADLINE_Q.format(headline=story["headline"])}}
    got = ask(_state(item), q)
    return got["changed"]["noul"] >= CHANGED, "headline" in q and got["headline"]["noul"] < HEADLINE_OK


def _reword(story: dict, stale: bool, bumped: bool, out: dict) -> None:
    """A stale headline on a story whose rev did not move: rewritten at the same rev (a bump rewrites it anyway)."""
    if stale and not bumped:
        db.ask_reword(story["id"])
        out["reworded"] += 1


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


# Kinds that are one kind of news for grouping: a signing or a release is "role" to one source and "depth" to the
# next (Jev split Dalton's move to Atlanta 71% role / 48% depth, 2026-10-19, and it became five stories).
KIND_FAMILY = {"depth": "role"}


def _same_kind(a: str | None, b: str | None) -> bool:
    return a is not None and KIND_FAMILY.get(a, a) == KIND_FAMILY.get(b, b)


def _joins(story: dict, kept: list[dict], prob: float | None) -> bool:
    """Jev's story pick is a proposal; it stands only when Jev is sure enough and the story shares a player
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


def _thread(cands: list[dict], kept: list[dict], now: datetime) -> tuple[dict, list[dict]] | None:
    """The story of a player's developing situation: one he leads (`_lead`), ▲/▼ on it with the same kind of news.
    Only his row is written, so a roundup's other players never land on someone else's story; and a roundup's other
    movers do not pull their own news into the roundup's story. With stories for several of its players, the item
    goes to the one of the player Jev is surest of (effect prob)."""
    since = (now - timedelta(hours=THREAD_HOURS)).isoformat()

    def his(c: dict, p: dict) -> bool:
        return (c["updated_at"] >= since and _lead(c) == p["player_id"]
                and any(r["player_id"] == p["player_id"] and r["effect"] != "none" and _same_kind(r["kind"], p["kind"])
                        for r in c["players"]))

    for p in sorted((p for p in kept if p["effect"] != "none"), key=lambda p: p["prob"], reverse=True):
        story = next((c for c in cands if his(c, p)), None)  # cands: newest first
        if story:
            return story, [p]
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


def _tag(item: dict, index: Index, ask, now: datetime, out: dict) -> None:
    at = now.isoformat()
    players = _players(item, index)
    if not players:
        _untag(item, None, at)
        out["tagged"] += 1
        return
    own = db.story(item["story_id"]) if item["story_id"] is not None else None
    cands = [] if own else _candidates([p["player_id"] for p in players], index, now)
    answers = ask(_state(item), questions(item, players, cands))
    got = read(answers, players)
    kept = got["players"]
    pids = [p["player_id"] for p in kept]
    if not kept:
        _untag(item, answers, at)
        out["tagged"] += 1
        return
    if own:  # an edit: its own story only, bumped when the new text says something new
        change, stale = weigh(own, item, ask)
        bumped = _attach(item, own, kept, change, False, at)
        db.tag_item(item["id"], pids, answers, own["id"], at)
        _reword(own, stale, bumped, out)
        out["tagged"] += 1
        out["updated"] += bumped
        return
    chosen = next((c for c in cands if c["id"] == got["story"]), None)
    if chosen and not _joins(chosen, kept, got["story_prob"]):
        chosen = None
    rows, threaded = kept, False
    if chosen is None and (thread := _thread(cands, kept, now)):
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
    # a retraction names the report it takes back: Jev did not pick a threaded story, so it cannot be corrected
    corrected = not older and not threaded and (got["retract"] or 0) >= RETRACT
    change, stale = weigh(chosen, item, ask) if not older and not corrected else (False, False)
    bumped = _attach(item, chosen, rows, change, corrected, at)
    db.tag_item(item["id"], pids, answers, chosen["id"], at)
    _reword(chosen, stale, bumped, out)
    out["tagged"] += 1
    out["attached"] += 1
    out["updated"] += bumped


def _down(e: Exception) -> bool:
    """The service failed, not this item: no answer at all (connection, timeout, no key) or a 5xx."""
    return isinstance(e, JevError) and (e.status is None or e.status >= 500)


def run(index: Index, ask=jev.ask, now: datetime | None = None) -> dict:
    """Tag every untagged item, oldest first. An item that fails on its own (Jev refuses its request, answers in
    a shape `read` cannot take, or fails with a 5xx once Jev has answered this run) is skipped and left untagged,
    and after TAG_MAX_TRIES failed runs tagged with no story (`given_up`). Jev down (two service failures before
    any answer: the first may be the item's own), the account failing (no key, a bad key, no credits, rate
    limited: JevError.account) or a DB error stops the run (`error`); the rest wait, uncounted. `usage` is what Jev
    read and answered this run, added to the day's in state `news_jev_usage`."""
    now = now or datetime.now(timezone.utc)
    out = {"tagged": 0, "new": 0, "attached": 0, "updated": 0, "reworded": 0, "failed": [], "given_up": [],
           "error": None, "usage": usage.zero()}
    up = False  # Jev answered this run, so a service failure is the item's
    early: list[int] = []  # items a service failure stopped before any answer: counted only once Jev is seen up

    def asked(state, qs):
        nonlocal up
        got = ask(state, qs)
        up = True
        bill = getattr(got, "usage", None) or {}
        usage.add(out["usage"], bill.get("input_tokens", 0), bill.get("output_tokens", 0))
        return got

    try:
        tries: dict[str, int] = db.get_state(TRIES, {}) or {}
        for item in db.untagged_items():
            try:
                _tag(item, index, asked, now, out)
            except db.DBError:
                raise
            except Exception as e:  # noqa: BLE001 — anything else is this item's (KeyError/TypeError: an odd answer)
                if isinstance(e, JevError) and e.account:
                    # the key or its credits: every item would fail the same, so none is counted towards giving up
                    out["error"] = str(e)
                    break
                if _down(e) and not up:
                    if early:
                        out["error"] = str(e)
                        break
                    early.append(item["id"])
                    continue
                up = up or not _down(e)  # Jev answered, if only to refuse
                out["failed"].append(item["id"])
            else:
                tries.pop(str(item["id"]), None)
        out["failed"] = sorted(out["failed"] + (early if up else []))
        at = now.isoformat()
        for iid in out["failed"]:
            n = tries[str(iid)] = tries.get(str(iid), 0) + 1
            if n >= TAG_MAX_TRIES:
                db.tag_item(iid, [], None, None, at)
                out["given_up"].append(iid)
                del tries[str(iid)]
        db.put_state(TRIES, tries)
        usage.record(usage.JEV, out["usage"], now)
    except db.DBError as e:
        out["error"] = f"db: {e}"
    return out
