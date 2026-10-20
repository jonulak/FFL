"""The news bench's expected cases: what a grouping or prompt change must keep true over the frozen slice.

A case is an id, a description, the players it watches (ids, or full names looked up in the frozen index) and a
check over the replay (`bench.Slice`) that returns (passed, the numbers). Under `bench --player` a case watching
none of the chosen players is skipped; one watching nobody (a league-wide rule) always runs. Adding a case is a
function and a line in CASES.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .bench import Slice

GRUPE = 4259619
HURTS = 4040715
IR = re.compile(r"\bIR\b|injured reserve", re.I)


@dataclass(frozen=True)
class Case:
    id: str
    description: str
    check: Callable[[Slice], tuple[bool, str]]
    players: tuple[int | str, ...] = ()


def _ids(stories: list[dict]) -> str:
    return " ".join(f"#{s['id']}" for s in stories)


def grupe_knee(s: Slice) -> tuple[bool, str]:
    led = [st for st in s.arrows(GRUPE, "down", "injury") if s.lead(st) == GRUPE]
    detail = f"{len(led)} ▼ injury stories led by him {_ids(led)}".rstrip()
    ok = len(led) <= 1
    if s.write:
        heads = [st["headline"] or "" for st in led]
        no_ir = [h for h in heads if not IR.search(h)]
        ok = ok and not no_ir
        detail += f"; headline {'IR' if heads and not no_ir else 'without IR'}: {heads[0] if heads else '-'!r}"
    return ok, detail


def adams_hamstring(s: Slice) -> tuple[bool, str]:
    pid = s.pid("Davante Adams")
    down = s.arrows(pid, "down", "injury")
    return len(down) <= 1, f"{len(down)} ▼ injury stories {_ids(down)}".rstrip()


def hurts_season(s: Slice) -> tuple[bool, str]:
    """`read` already turns a passing mention (fresh under tag.FRESH) to ●, so the second rule is checked where it can
    fail: every story he is ▼ on has at least one item where he is fresh and ▼, not passing mentions alone."""
    down = s.arrows(HURTS, "down")
    hollow = [st for st in down if not any(HURTS in s.movers(i, "down") for i in st["items"])]
    detail = f"{len(down)} ▼ stories {_ids(down)}".rstrip()
    if hollow:
        detail += f"; no fresh ▼ item in {_ids(hollow)}"
    return len(down) <= 3 and not hollow, detail


def no_mixed_threads(s: Slice) -> tuple[bool, str]:
    """The grouping work's rule (joins.py): an item a story took by thread (neither its founder nor Jev's own pick)
    must be its lead's news: its surest ▲/▼ player is the story's lead. A Jev pick that adds a roundup's other
    movers is not a thread."""
    mixed = []
    for st in s.stories:
        lead = s.lead(st)
        for i in s.threaded(st):
            top = s.surest(i)
            if top is not None and top != lead:
                mixed.append((st, i, top))
    detail = f"{len(mixed)} threaded items whose news is another player's"
    if mixed:
        detail += ": " + "; ".join(f"#{st['id']} (lead {s.name(s.lead(st))}) took {s.name(top)}'s "
                                   f"{(i['title'] or '')[:50]!r}" for st, i, top in mixed[:5])
    return not mixed, detail


# (the namesake in the text, the player it must not move, the word only the player's own mention has)
COACHES = (("Ben Johnson", "Roschon Johnson", "roschon"), ("Lavonte David", "David Sills", "sills"))


def coach_not_player(s: Slice) -> tuple[bool, str]:
    """A story's item naming the coach (or the linebacker) and not the player never moves the player."""
    seen, wrong = 0, []
    for other, name, own in COACHES:
        pid = s.pid(name)
        for st in s.stories:
            for i in st["items"]:
                text = f"{i['title']}\n{i['text']}"
                if other in text and own not in text.lower():
                    seen += 1
                    if pid in s.movers(i):
                        wrong.append(f"{name} ▲/▼ on #{st['id']} {(i['title'] or '')[:50]!r}")
    return not wrong, f"{seen} items naming the namesake, {len(wrong)} ▲/▼" + (f": {'; '.join(wrong)}" if wrong else "")


CASES = [
    Case("grupe-knee", "Blake Grupe's knee (limps off, doubtful, out, IR) is one story he leads; written, its "
                       "headline says IR", grupe_knee, (GRUPE,)),
    Case("adams-hamstring", "Davante Adams's hamstring is one ▼ injury story", adams_hamstring, ("Davante Adams",)),
    Case("hurts-season", "Jalen Hurts's blood clots are at most 3 ▼ stories, none made of passing mentions",
         hurts_season, (HURTS,)),
    Case("no-mixed-threads", "no story threads in another player's news", no_mixed_threads),
    Case("coach-not-player", "coach Ben Johnson never moves Roschon Johnson; Lavonte David never moves David Sills",
         coach_not_player, ("Roschon Johnson", "David Sills")),
]
