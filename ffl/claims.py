"""Waiver claim groups: "get K of these N" as instant free-agent adds plus the ordered claim list ESPN runs (pure).

ESPN has no K-of-N claim. A claim is one transaction, an add plus at most one drop, and it cancels when its add or
its drop is gone (journal/DECISIONS.md 2026-09-22). So claims that share a drop exclude each other: every (target,
row) pair in rank order lands each target at most once and uses each row at most once; K = rows = cuts + open spots.
A free agent needs no claim: he is added on Submit, in rank order, taking the group's next row, and the claims get
the rows left. A no-drop claim lands while the roster has room, so open spots belong to one group and it takes all
of them; otherwise another group's no-drop claim could fill a spot this group counted on. A roster player may be a cut
in several groups: claims sharing a drop exclude each other anyway, so it reads "drop him for group 1, else for group
2"; once a free-agent add has dropped him he is gone from every group's rows."""
from __future__ import annotations

from collections import Counter

from . import decisions
from .season import ir_illegal

FREE = "FREEAGENT"


def open_spots(roster, roster_size):
    """Count open roster spots, excluding IR."""
    return max(0, roster_size - sum(1 for p in roster if p.get("slot") != "IR"))


def rooms(group):
    """The order rows are used in: open spots first, then cuts."""
    return [None] * group.get("open", 0) + list(group["cuts"])


def capacity(group):
    """The max claims a group can win: len(rooms)."""
    return len(rooms(group))


def free_targets(group, status):
    """Targets in this group that are free agents, in rank order."""
    return [t for t in group["targets"] if status.get(t) == FREE]


def waiver_targets(group, status):
    """Targets in this group that require claims, in rank order."""
    return [t for t in group["targets"] if status.get(t) != FREE]


def plan_now(groups, status):
    """Instant adds: free agents taking their group's first rows, in rank order (a cut an earlier add dropped is gone)."""
    out, gone = [], set()
    for gi, g in enumerate(groups):
        for t, d in zip(free_targets(g, status), [d for d in rooms(g) if d not in gone]):
            out.append({"group": gi, "add": t, "drop": d})
            if d is not None:
                gone.add(d)
    return out


def expand(groups, status, now=None):
    """Waiver claims over each group's WAIVERS targets x rooms left after the instant adds.

    now: the instant adds that landed (run_submit's: a refused one gives its row back); defaults to plan_now. A group
    loses the open spots its own adds took and every cut any add dropped.
    """
    if now is None:
        now = plan_now(groups, status)
    gone = {m["drop"] for m in now if m["drop"] is not None}
    out = []
    for gi, g in enumerate(groups):
        taken = sum(1 for m in now if m["group"] == gi and m["drop"] is None)
        left = [None] * max(0, g.get("open", 0) - taken) + [c for c in g["cuts"] if c not in gone]
        for t in waiver_targets(g, status):
            seen_open = False
            for d in left:
                # One no-drop claim per target is enough; open spots (None) are valuable.
                if d is None:
                    if seen_open:
                        continue
                    seen_open = True
                out.append({"group": gi, "add": t, "drop": d})
    return out


INSTANT_OFF = "instant adds are switched off (FFL_INSTANT_ADDS=0)"
IR_BLOCK = "on IR without an injury designation: ESPN allows no add or claim until he is off IR"


def cap_reason(p, held, cap):
    return f"you hold {held} {p} of ESPN's cap of {cap}: he can't land without a {p} cut"


def validate(groups, roster, pool_ids, roster_size, status, instant_adds=True, owners=None, pos=None, limits=None):
    """Check the plan for errors. Returns list of dicts: {"group": int, "player_id": int|None, "reason": str}.

    instant_adds False (FFL_INSTANT_ADDS off): every free-agent target is an error, since Submit would add him.
    An IR player with no injury designation (season.ir_illegal) is a roster error (group None): ESPN refuses every
    add and claim until he is off IR. An empty plan carries none, so the claims it sent can still be withdrawn.
    pos (player_id -> position) and limits (settings `position_limits`): a target at a capped position I already
    hold the cap of can land only by dropping one of his position, so a group with no such cut never gets him."""
    errs = []
    pos, limits = pos or {}, limits or {}
    held = Counter(p.get("pos") for p in roster)

    def err(gi, pid, reason):
        errs.append({"group": gi, "player_id": pid, "reason": reason})

    if groups:
        for p in ir_illegal(roster):
            err(None, p["player_id"], IR_BLOCK)

    by_id = {p["player_id"]: p for p in roster}
    free = open_spots(roster, roster_size)
    targets, cuts, open_groups = {}, {}, []

    for gi, g in enumerate(groups):
        n = gi + 1
        op = g.get("open", 0)
        instant = bool(free_targets(g, status))

        # Empty group checks.
        if not g["targets"]:
            err(gi, None, f"group {n} has no targets")
        if capacity(g) == 0:
            err(gi, None, f"group {n} has nothing to make room: add a cut")

        # Open spots validation.
        if op:
            open_groups.append(gi)
        if op > free:
            err(gi, None, f"group {n} uses more open spots ({op}) than the roster has ({free})")
        elif op and op != free:
            err(gi, None, f"group {n} must use all {free} open spots, or none")

        # Target validation.
        for t in g["targets"]:
            if t in targets:
                err(gi, t, f"already a target in group {targets[t] + 1}")
            targets.setdefault(t, gi)
            if t in by_id:
                err(gi, t, "already on your roster")
            elif (owners or {}).get(t):
                err(gi, t, f"taken by {owners[t]}; remove him from the group")
            elif t not in pool_ids:
                err(gi, t, "not in the free-agent pool")
            if not instant_adds and status.get(t) == FREE:
                err(gi, t, INSTANT_OFF)
            p, cap = pos.get(t), limits.get(pos.get(t))
            if cap is not None and held[p] + 1 > cap and (held[p] > cap or not any(pos.get(c) == p for c in g["cuts"])):
                err(gi, t, cap_reason(p, held[p], cap))

        # Cut validation.
        for i, c in enumerate(g["cuts"]):
            if c in g["cuts"][:i]:
                err(gi, c, "listed twice in this group's cuts")
            cuts.setdefault(c, gi)  # another group may cut him too: "drop him for group 1, else for group 2"
            p = by_id.get(c)
            if p is None:
                err(gi, c, "not on your roster")
                continue
            if p.get("slot") == "IR":
                err(gi, c, "on IR: he frees no roster spot")
            if p.get("droppable") is False:
                err(gi, c, "undroppable (ESPN's undroppable list)")
            if instant and p.get("locked"):
                err(gi, c, "his game has started: he can't be dropped for a free agent until the week rolls")

    # Overlap checks: a player can't be both a target and a cut.
    for pid in set(targets) & set(cuts):
        err(targets[pid], pid, "both a target and a cut")

    # Only one group can use open spots.
    if len(open_groups) > 1:
        err(open_groups[1], None, "only one group can use the open spots")

    return errs


def cap_warnings(groups, roster, pos, limits):
    """Groups whose wins could run past a position cap: same shape as validate's errors, but not blocking, since ESPN
    refuses only the claim that would go over and the group's next target takes its row. A group's wins use its rooms
    in order (open spots, then cuts), so n wins net at most min(n, its targets there) minus the cuts there in its
    first n rooms; earlier groups' worst cases are counted in (a cut two groups share, once per group)."""
    held = Counter(p.get("pos") for p in roster)
    run, out = Counter(), []
    for gi, g in enumerate(groups):
        r, k = rooms(g), min(capacity(g), len(g["targets"]))
        for p, cap in limits.items():
            tp = sum(1 for t in g["targets"] if pos.get(t) == p)
            if not tp or k == 0:
                continue
            worst = max(min(n, tp) - sum(1 for d in r[:n] if d is not None and pos.get(d) == p) for n in range(1, k + 1))
            run[p] += max(0, worst)
            if held[p] + 1 > cap and (held[p] > cap or not any(pos.get(c) == p for c in g["cuts"])):
                continue  # validate's error: none of his can land at all
            if held[p] + worst > cap:
                out.append({"group": gi, "player_id": None, "reason":
                            f"{p}: you hold {held[p]} of ESPN's cap of {cap} and this group could land {worst} more; "
                            f"ESPN refuses the claim that goes over and the next target takes its row"})
            elif held[p] + run[p] > cap:
                out.append({"group": gi, "player_id": None, "reason":
                            f"{p}: with the groups above, up to {held[p] + run[p]} against ESPN's cap of {cap}; "
                            f"ESPN refuses the claim that goes over"})
    return out


def decision_row(groups, names, status, week, submits, gen=0):
    """The plan's claims row in the decisions table (db.put_week_claims keeps one per week and plan generation `gen`,
    the count of plans the waiver run retired this week, updated by each submit)."""
    def nm(i):
        return names.get(i, str(i)) + (" (FA, added now)" if status.get(i) == FREE else "")

    parts = []
    for i, g in enumerate(groups):
        part = (f"({i + 1}) get {capacity(g)} of {len(g['targets'])}: {', '.join(map(nm, g['targets']))}; "
                f"cut {', '.join(names.get(c, str(c)) for c in g['cuts']) or 'none'}")
        if g.get("open"):
            part += f" + {g['open']} open spot(s)"
        parts.append(part)
    ids = list(dict.fromkeys(p for g in groups for p in g["targets"] + g["cuts"]))
    return {"at": decisions.now_iso(), "week": week, "kind": "claims", "source": "app",
            "title": f"Week {week} waiver claims", "decision": "; ".join(parts),
            "facts": {"groups": groups, "submits": submits, "gen": gen}, "rationale": None, "player_ids": ids}


def free_add_rows(now, names, week):
    """One decisions row per free agent a submit added on the spot."""
    out = []
    for m in now:
        title = f"Week {week} add: {names.get(m['add'], str(m['add']))}"
        if m.get("drop") is not None:
            title += f", drop {names.get(m['drop'], str(m['drop']))}"
        out.append({"at": decisions.now_iso(), "week": week, "kind": "free_add", "source": "app", "title": title,
                    "decision": title.split(": ", 1)[1], "facts": {"move": m}, "rationale": None,
                    "player_ids": [m["add"]] + ([m["drop"]] if m.get("drop") is not None else [])})
    return out


def matches(claims, pending):
    """Same (add, drop) sequence in the same order: ESPN runs claims by priority, so order is part of the plan."""
    return [(c["add"], c["drop"]) for c in claims] == [(c["add"], c["drop"]) for c in pending]


def run_submit(groups, status, pending, last_ids, replace_outside, writer):
    """Free agents first (instant, rank order, each on the group's next row; a refused add leaves its row for the next),
    then clear and repost the whole claim list over the rows left, so a retry after any failure is the same call. Stops
    the claims at the first failed write and still reads ESPN back: the page shows what ESPN holds, never what we hoped."""
    now, now_failed = [], []
    for gi, g in enumerate(groups):
        gone = {m["drop"] for m in now if m["drop"] is not None}  # a cut an earlier group's add dropped
        r, n = [d for d in rooms(g) if d not in gone], 0
        for t in free_targets(g, status):
            if n >= len(r):
                break
            try:
                writer.add_free_agent(t, r[n])
                now.append({"group": gi, "add": t, "drop": r[n]})
                n += 1
            except Exception as e:  # noqa: BLE001 — taken or refused: his row goes to the next target
                now_failed.append({"group": gi, "add": t, "drop": r[n], "error": str(e)})
    claims = expand(groups, status, now)
    # outside claims (filed on ESPN, not by this app) kept on purpose run ahead of ours
    kept_outside = [] if replace_outside else [c["id"] for c in pending if c["id"] not in last_ids]
    sent, error = [], None
    try:
        for c in pending:
            if c["id"] in last_ids or replace_outside:
                writer.cancel_claim(c["id"])
        for c in claims:
            sent.append({**c, "id": writer.submit_claim(c["add"], c["drop"])})
        # ESPN does not number a claim the API posts: number them all, outside ones first, ours in plan order
        if sent:
            writer.reorder_claims(kept_outside + [c["id"] for c in sent])
    except Exception as e:  # noqa: BLE001 — reported in the result, with the read-back
        error = str(e)
    try:
        after, readback_failed = writer.pending_claims(), False
    except Exception as e:  # noqa: BLE001 — the writes landed; the caller must still journal them
        after, readback_failed = [], True
        error = error or f"read-back failed: {e}"
    # match ours, in order, without the kept outside claims; a claim ESPN read back unnumbered (priority None) means the
    # order is unknown, so it is never a match
    ours = [c for c in after if c["id"] not in set(kept_outside)]
    numbered = all(c.get("priority", 0) is not None for c in after)
    return {"now": now, "now_failed": now_failed, "sent": sent, "pending": after,
            "match": error is None and numbered and matches(claims, ours), "error": error, "readback_failed": readback_failed}


def retire(plan, last_run):
    """The plan once ESPN's waiver run has processed it: a plan sent and not saved since the run (`last_run`, local
    ISO; None when unknown) moves to `retired` and the week starts empty. The plan file is per ESPN week, which rolls
    days after the run, so without this the won targets sit in it as "already on your roster"."""
    if not plan["submits"] or last_run is None:
        return plan
    stamp = max(plan.get("saved_at") or "", plan["submits"][-1]["at"])
    if last_run <= stamp:
        return plan
    old = {"run": last_run, "groups": plan["groups"], "submits": plan["submits"]}
    return {**plan, "groups": [], "submits": [], "retired": plan.get("retired", []) + [old]}


def consume(groups, now):
    """The plan minus what instant adds already did: a landed target and its row leave their group (a no-drop row is an
    open spot), and a group with no targets left is dropped. `now` indexes the groups as submitted."""
    out = [{**g, "targets": list(g["targets"]), "cuts": list(g["cuts"])} for g in groups]
    for m in now:
        g = out[m["group"]]
        if m["add"] in g["targets"]:
            g["targets"].remove(m["add"])
        if m["drop"] is None:
            g["open"] = max(0, g["open"] - 1)
        for o in out:  # he is off the roster: gone from every group that cut him
            if m["drop"] in o["cuts"]:
                o["cuts"].remove(m["drop"])
    return [g for g in out if g["targets"]]
