"""The news desk's tagger as rules: one item's answers from its text, his recent statuses and the depth chart.

Same input, same answers. Each player's status comes from the sentences naming him, on a ladder (STATUS: rank, lower is
worse). His arrow is the step from his last status in his stories (`last_status`), so "limited" reads ▼ from healthy
and ▲ from a DNP; the same status again is not news. With no history the status's own direction stands. PROB is a rule's
agreement with the Haiku tagger it replaced, per event (issue #120), so a rule that is often wrong sits under
notices.NEWS_PUSH_PROB and never pushes. The answers keep the shape `tag.read` takes, with `status:<pid>` added for the
next item and `chart:<pid>` on a player the depth chart lifted (`lifted`), whom the item need not name.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .. import news
from ..adp import norm_name

MEDIA = ("yt:", "pod:")  # spoken text: a name and a status word in one breath are not news about him; ● only
SUFFIX = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}
LEAD_CHARS = 300  # the opening of an item: a player named here is what it is about
SHARE = 1 / 3  # of its sentences naming him, past which an item is about him wherever he is first named
# word overlap over which an item reports the same development as a source. Bge-small vectors did no better against
# Haiku's stories (0.42 precision at words 0.15, 0.44 at cosine 0.85, recall 0.23 both), and need no model at tag time
SIMILAR = 0.2  # 0.47 of the picks over it were Haiku's story too: `tag._joins` is what keeps a wrong one out
AGAIN_CHARS = 25
CHART_PROB = 0.75  # no chart history to measure it on: under the push cutoff until real weeks are checked (issue #120)
CHART_HOURS = 48  # the chart is today's: a lift only for news this recent
GONE = {"ir", "out", "released"}  # the statuses that open a job

# status -> (rank, effect with no history, kind). Rungs share a rank when neither is a step from the other.
STATUS = {"ir": (0, "down", "injury"), "released": (0, "down", "role"), "out": (1, "down", "injury"),
          "benched": (2, "down", "role"), "doubtful": (2, "down", "injury"), "dnp": (3, "down", "injury"),
          "hurt": (3, "down", "injury"), "limited": (4, "down", "injury"), "full": (5, "none", "return"),
          "active": (6, "up", "return"), "starter": (6, "up", "role"), "signed": (6, "up", "role")}
LIMITED = STATUS["limited"][0]
# Most severe first: a sentence with "limited Wednesday, out Sunday" stands at out.
PATTERNS = [
    ("active", r"\b(from|off) (the )?(IR\b|injured reserve|reserve/injured|PUP\b|NFI\b)|\bdesignated (\w+ )?(to|for) return"),
    ("ir", r"\b(be|being) placed on\b|(placed|placing|put|landed|going|goes|headed|moved?|moving|lands?) (\w+ ){0,3}(on|to) (the )?(IR\b|injured reserve|reserve/injured|PUP\b|NFI\b)"
           r"|\bon (IR\b|injured reserve)|season.ending|out for the (season|year)|miss the (rest of the )?(season|year)|\b(torn|tore) (his |an? )?(ACL|achilles|MCL|pec)"),
    ("released", r"\b(released?|releasing|waived?|waiving|cut by|parted ways|part ways)\b"),
    ("out", r"\brule[sd]? (\w+ ){0,3}out\b|\bout \d+(-\d+)? (weeks|months)\b|\bwill(?: not|n'?t) play\b|\bwon'?t play\b|\binactive\b|\bout (for |on |against |vs\.? )(sunday|monday|thursday|saturday|week|the game)"
            r"|\b(will|set to|expected to|going to|likely to) miss\b|\bdid(?: not|n'?t) return\b|\bwon'?t return\b|\bcarted\b|\bis out\b"),
    ("benched", r"\b(benched|demoted|lost (his|the) (starting|job))\b"),
    ("doubtful", r"\bdoubtful\b"),
    ("dnp", r"\b(did(?: not|n'?t)|won'?t|will not|not expected to|unable to) (practice|participate)|\bnot practic|\bnot seen\b|\bDNP\b|\bmiss(es|ed)? (\w+ )?practice|\bsat out\b|\bno reps\b"),
    ("hurt", r"\b(left|exited) (the game|sunday|monday|thursday|saturday|with|early)|\bsuffered\b|\bsustained\b|\baggravated\b|\bmedical tent\b|\binjury tent\b"
             r"|\blimp(ed|ing)\b|\bsprain|\bfractur|\bsurgery\b|\bhospital\b|\bwalking boot\b|\bconcussion protocol\b"),
    ("limited", r"\blimited\b|\bquestionable\b"),
    ("full", r"\bfull(y)? (participant|practices?|go)\b|\bpracticed (fully|in full)\b|\bno limitations\b"),
    ("active", r"\bis active\b|\blisted as active\b|\b(activated|activate|reinstated|designated (\w+ )?to return)\b|\breturn(s|ed)? to (full )?practice\b|\b(back at|will) practice\b|\bcleared\b"
               r"|\b(off|removed from) (the )?injury report\b|\bno injury designation\b|\b(expected|set|slated|ready) to (play|return)\b|\bwill (play|return)\b"),
    ("starter", r"\bgets? the start\b|\b(named|will|to) (the )?start(er|ing)?\b|\bwill start\b|\btakes? over\b|\blead back\b|\bstarting job\b|\bpromoted\b|\bNo\. 1\b"),
    ("signed", r"\b(signs?|signed|signing|re-signed|re-signing|adds?|added)\b"),
]
PATTERNS = [(s, re.compile(p, re.I)) for s, p in PATTERNS]
AGAIN = re.compile(r"\b(again|second|third|fourth|another|straight|consecutive)\b", re.I)  # right after a status: no new step
# not this news: it did not happen, might not, or is his past
UNREAL = re.compile(r"\b(no|not|again|whether|possibl\w+|optimistic|remains?|still|avoid\w*|without|never|if|unless|could|may|might|after|since|last (week|season|year)|previous|earlier|before|despite|in 20\d\d)\b[^,;]{0,40}$", re.I)
ROUNDUP = re.compile(r"\b(takeaways?|recap|preview|film|top plays|highlights|inactives|by the numbers|observations|things to (watch|know)|quick hits|mailbag|power rankings|"
                     r"notebook|what we learned|winners and losers|transcript|press conference|postgame|interviews?|podcast|updates?\b.*quarter|\d+,? \w+ \d+)\b", re.I)
STATLINE = re.compile(r"\b(rushed|caught|carried|completed|brought in|hauled in|finished with|receptions?|yards)\b", re.I)
SUBJECT_WORDS = 6
MOVES = re.compile(r"\b(roster moves?|transactions?|following moves|roster update)\b", re.I)
STRONG = {"ir", "released", "out", "active"}  # enough for a roundup to report on him
ROLE = re.compile(r"\b(starter|snaps|targets|carries|workload|depth chart|role|backup|rotation|touches)\b", re.I)
COACH = r"(coach|HC|OC|DC|coordinator|GM|owner|president|analyst|reporter)"
POSITION = r"(QB|RB|WR|TE|K|P|S|CB|LB|DE|DT|OT|OG|C|FB|OL|DL|EDGE)"
RETRACT = re.compile(r"\b(retract\w*|walk(ed|ing)? back|correction|corrected|earlier report (was|is) (wrong|incorrect|false)|erroneous\w*|misreported|was mistaken)\b", re.I)
WORD = re.compile(r"[a-z0-9']+")
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "at", "by", "with", "from", "is", "was", "are", "were", "be", "has",
        "had", "have", "he", "his", "him", "it", "its", "that", "this", "as", "not", "but", "will", "after", "said", "says", "reports"}
CLAUSE = re.compile(r"[,;:]|\b(?:and|but|while|with|as|behind|after|following|because|in the wake of|replac\w+)\b")


def surname(name: str) -> str:
    words = [w for w in name.split() if w.lower() not in SUFFIX]
    return words[-1] if words else name


def _sentences(text: str) -> list[str]:
    # no split after an initial or an abbreviation: "A.J. Brown", "vs. Arizona"
    return [s.strip() for s in re.split(r"(?<![A-Z])(?<!\bvs)(?<!\bNo)[.!?]+[\"”']?\s+|\n+", text or "") if s.strip()]


class _Named:
    """A real mention of him in a line: the surname as a word of its own, capitalized as a name is, with his first name
    before it or nobody else's. "Cam Taylor-Britt", "Akayleb Evans" and "Taylor Moton" are not Jonathan, Mitchell or Tyrod."""

    def __init__(self, name: str):
        words = [w for w in name.split() if w.lower() not in SUFFIX]
        self.first = words[0] if len(words) > 1 else ""
        self.pat = re.compile(rf"(?<![\w'’.-]){re.escape(surname(name))}(?![\w-])")

    def search(self, line: str) -> bool:
        return any(self._his(line, m) for m in self.pat.finditer(line))

    def _his(self, line: str, m: re.Match) -> bool:
        before, after = line[:m.start()].split(), line[m.end():].split()
        prev = before[-1] if before else ""
        if prev.rstrip(".") == self.first.rstrip("."):
            return True
        if prev and prev[0].isupper() and not prev.isupper() and prev[-1] not in ",:;.'’s":
            return False  # someone else's first name, or a team name standing where his would
        if after and after[0].rstrip(".,;:").lower() in SUFFIX:
            return True
        return not (after and after[0][0].isupper() and after[0][0].isalpha() and m.end() < len(line) and line[m.end()] == " "
                    and not after[0].isupper())


def _named(name: str) -> _Named:
    return _Named(name)


def scope(item: dict, player: dict, others: list[dict]) -> list[str]:
    """What the item says of him: his sentences and a He/His one right after; beside another named player, only the
    clause naming him, so one man's injury never lands on the next."""
    me = _named(player["name"])
    rest = [_named(o["name"]) for o in others if o["player_id"] != player["player_id"]]
    out: list[str] = []
    mine = False
    for s in [item.get("title") or "", *_sentences(item.get("text") or "")]:
        if me.search(s):
            mine = True
            out += [c for c in CLAUSE.split(s) if me.search(c)]
        elif mine and re.match(r"(he|his)\b", s, re.I) and not any(r.search(s) for r in rest):
            out.append(s)
        else:
            mine = False
    return out


def arrow_lines(item: dict, player: dict, others: list[dict]) -> list[list[str]]:
    """Where his status may be read, in order: the title if it names him, then the lead's sentences naming him. A status
    deep in an article or in a roundup is rarely today's news about him, so neither gives an arrow; a roster-move list
    is the exception, every line of it is news."""
    title = item.get("title") or ""
    if ROUNDUP.search(title) or STATLINE.search(title):
        return []
    lines = scope(item, player, others)
    if MOVES.search(title):
        return [lines]
    me = _named(player["name"])
    in_title = bool(me.search(title))
    if not in_title and any(_named(o["name"]).search(title) for o in others if o["player_id"] != player["player_id"]):
        return []  # a note on another player
    lead = (item.get("text") or "")[:LEAD_CHARS]
    # in the body he must open the sentence: its subject, not someone it mentions
    return [[s for s in lines if s in title], [s for s in lines if s in lead and me.search(" ".join(s.split()[:SUBJECT_WORDS]))]]


def status_of(lines: list[str]) -> str | None:
    """The worst status his lines report as real and current."""
    for name, pat in PATTERNS:
        for line in lines:
            for m in pat.finditer(line):
                if not UNREAL.search(line[:m.start()]) and not AGAIN.search(line[m.end():m.end() + AGAIN_CHARS]):
                    return name
    return None


def about(item: dict, player: dict) -> bool:
    """False when each mention of the name is a coach or a player at another position."""
    text = f"{item.get('title') or ''}\n{item.get('text') or ''}"
    name = re.escape(surname(player["name"]))
    me = _named(player["name"])
    named = [ln for ln in re.split(r"(?<=[.!?])\s+|\n+", text) if me.search(ln)]
    if not named:
        return False
    wrong = re.findall(rf"\b{COACH}\s+(?:[A-Z][\w.'-]+\s+){{0,2}}{name}\b", text)
    wrong += [m for m in re.findall(rf"\b{POSITION}\s+(?:[A-Z][\w.'-]+\s+){{0,2}}{name}\b", text) if m != player["pos"]]
    return len(wrong) < len(named)


def subject(item: dict, player: dict, lines: list[str], status: str | None) -> bool:
    """A report on him, not a passing mention: a roundup needs a strong status of his own; else he is in the title or
    the lead, or a share of its sentences."""
    title = item.get("title") or ""
    if ROUNDUP.search(title):
        return status in STRONG
    me = _named(player["name"])
    text = item.get("text") or ""
    if me.search(title) or me.search(text[:LEAD_CHARS]):
        return True
    return len(lines) / max(1, len(_sentences(text))) >= SHARE


def last_status(pid: int, candidates: list[dict], before: str) -> str | None:
    """His newest recorded status before this item, in the stories he is on."""
    seen = [(i["published_at"], (i.get("jev") or {})[f"status:{pid}"]["status"]) for c in candidates for i in c.get("items") or []
            if i["published_at"] < before and f"status:{pid}" in (i.get("jev") or {})]
    return max(seen, key=lambda s: s[0])[1] if seen else None


def arrow(status: str | None, last: str | None) -> tuple[str, bool]:
    """(effect, fresh). No status is no news. The same rank as his last is not new; otherwise the step, or with no
    history the status's own direction."""
    if status is None:
        return "none", False
    rank, own, _ = STATUS[status]
    if last is None:
        return own, True
    was = STATUS[last][0]
    # out after IR is no recovery; full after active no setback
    if rank == was or (rank > was and own == "down" and rank < LIMITED) or (rank < was and own != "down"):
        return "none", False
    return ("up" if rank > was else "down"), True


def kind_of(status: str | None, effect: str, lines: list[str]) -> str:
    if status is not None:
        kind = STATUS[status][2]
        return "return" if kind == "injury" and effect == "up" else kind
    return "role" if any(ROLE.search(s) for s in lines) else "other"


# Agreement with Haiku per event on 2026-10-17..25. Under 0.8, too rarely right to push (or, benched, never seen)
PROB = {"ir": 0.83, "released": 0.95, "out": 0.83, "doubtful": 0.85, "dnp": 0.83, "hurt": 0.94, "full": 0.93, "active": 0.83,
        "limited": 0.6, "starter": 0.6, "signed": 0.6, "benched": 0.6}


def words(text: str) -> set[str]:
    return {w for w in WORD.findall(norm_name(text)) if w not in STOP}


def similarity(a: str, b: str) -> float:
    x, y = words(a), words(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _lead(item: dict) -> str:
    return f"{item.get('title') or ''} {(item.get('text') or '')[:LEAD_CHARS]}"


def pick_story(item: dict, players: list[dict], candidates: list[dict]) -> tuple[str, float]:
    """The story with a source about a player this title also names that reads most like this item, over SIMILAR, else
    `new`; a tie goes to the newest (candidates: newest first)."""
    best, top = "new", 0.0
    named = [p for p in players if _named(p["name"]).search(item.get("title") or "")]
    for c in candidates:
        for i in c.get("items") or []:
            ids = i.get("player_ids") or []
            if any(p["player_id"] in ids and _named(p["name"]).search(i.get("title") or "") for p in named):
                got = similarity(_lead(item), _lead(i))
                if got > top and got >= SIMILAR:
                    best, top = f"s{c['id']}", got
    return best, top


def lifted(x: dict, index, chart: dict[str, list[dict]]) -> dict | None:
    """Who moves up with X out: the first healthy man in his slot, if X mattered there. He mattered at order 1, or
    above the slot's order 1 in ownership, since Sleeper moves a hurt man down the chart. {player_id, name} or None."""
    rows = [r for r in chart.get(x["team"]) or [] if r["pos"] == x["pos"]]
    me = norm_name(x["name"])
    mine = next((r for r in rows if norm_name(r["name"]) == me), None)
    if mine is None:
        return None
    slot = sorted((r for r in rows if r["slot"] == mine["slot"]), key=lambda r: r["order"])
    ids = {(norm_name(v["name"]), v["team"]): pid for pid, v in index.by_id.items()}
    top = slot[0]
    if top is not mine:
        them = ids.get((norm_name(top["name"]), x["team"]))
        if them is None or index.owned.get(x["player_id"], 0) <= index.owned.get(them, 0):
            return None
    for r in slot:
        pid = ids.get((norm_name(r["name"]), x["team"]))
        if r is not mine and pid is not None and not r.get("injury"):
            return {"player_id": pid, **index.by_id[pid]}
    return None


def _noul(v: float) -> dict:
    return {"type": "noul", "noul": v}


def _choice(choice: str, p: float) -> dict:
    return {"type": "choice", "choice": choice, "probabilities": {choice: p}, "confidence": p}


def answer(item: dict, players: list[dict], candidates: list[dict], index=None, chart=news.fetch_depth,
           now: datetime | None = None) -> dict:
    """The item's answers, keyed as `tag.read` takes them. With `index` and `now`, a fresh out for a player who mattered
    adds the next man up. `chart` is called only then: the fetch is cached and most items never need it."""
    media = (item.get("source_id") or "").startswith(MEDIA)
    out: dict = {}
    published = item.get("published_at") or ""
    lifts: list[dict] = []
    for p in players:
        pid = p["player_id"]
        lines = scope(item, p, players)
        status = next((st for part in arrow_lines(item, p, players) if (st := status_of(part))), None)
        last = last_status(pid, candidates, published)
        effect, fresh = arrow(status, last)
        if media:
            effect, fresh = "none", False
        # spoken, he goes by his first name: the excerpt was cut for its owner (media.py), so it is about him whatever it says
        mine = media and (item.get("ext_id") or "").rpartition("#")[2] == str(pid)
        is_about, is_subject = mine or about(item, p), mine or subject(item, p, lines, status)
        prob = 1.0 if effect == "none" else PROB[status]
        out[f"about:{pid}"] = _noul(float(is_about))
        out[f"subject:{pid}"] = _noul(float(is_subject))
        out[f"fresh:{pid}"] = _noul(float(fresh))
        out[f"effect:{pid}"] = _choice(effect, prob)
        out[f"kind:{pid}"] = _choice(kind_of(status, effect, lines), prob)
        if status is not None and not media:
            out[f"status:{pid}"] = {"type": "status", "status": status}
        if effect == "down" and status in GONE and is_about and is_subject:
            lifts.append(p)
    if candidates:
        choice, _ = pick_story(item, players, candidates)
        out["story"] = _choice(choice, float(choice != "new"))
        out["retract"] = _noul(float(bool(RETRACT.search(_lead(item)))))
    if lifts and index is not None and now and datetime.fromisoformat(published) >= now - timedelta(hours=CHART_HOURS):
        depth = chart()
        named = {p["player_id"] for p in players}
        for x in lifts:
            y = lifted(x, index, depth)
            if y is not None and y["player_id"] not in named:
                yid = y["player_id"]
                out[f"about:{yid}"], out[f"subject:{yid}"], out[f"fresh:{yid}"] = _noul(1.0), _noul(1.0), _noul(1.0)
                out[f"effect:{yid}"], out[f"kind:{yid}"] = _choice("up", CHART_PROB), _choice("depth", CHART_PROB)
                out[f"chart:{yid}"] = {"type": "chart", "behind": x["player_id"]}
    return out
