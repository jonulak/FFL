"""Waiver claim groups: "get K of these N" as instant free-agent adds plus the ordered claim list ESPN runs (pure).

ESPN has no K-of-N claim. A claim is one transaction, an add plus at most one drop, and it cancels when its add or
its drop is gone (journal/DECISIONS.md 2026-09-22). So claims that share a drop exclude each other: every (target,
row) pair in rank order lands each target at most once and uses each row at most once; K = rows = cuts + open spots.
A free agent needs no claim: he is added on Submit, in rank order, taking the group's next row, and the claims get
the rows left. A no-drop claim lands while the roster has room, so open spots belong to one group and it takes all
of them; otherwise another group's no-drop claim could fill a spot this group counted on."""
from __future__ import annotations

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
    """Instant adds: free agents taking their group's first rows, in rank order."""
    out = []
    for gi, g in enumerate(groups):
        for t, d in zip(free_targets(g, status), rooms(g)):
            out.append({"group": gi, "add": t, "drop": d})
    return out


def expand(groups, status, used=None):
    """Waiver claims over each group's WAIVERS targets x rooms left after plan_now.

    used: dict[group_index, rows_consumed_by_plan_now]; defaults to min(free_targets, capacity) per group.
    """
    out = []
    for gi, g in enumerate(groups):
        r = rooms(g)
        # How many rows did plan_now take from this group?
        if used is not None:
            n = used.get(gi, 0)
        else:
            n = min(len(free_targets(g, status)), len(r))
        left = r[n:]
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


INSTANT_OFF = "instant adds are off until verified (ffl-ut0.7)"


def validate(groups, roster, pool_ids, roster_size, status, instant_adds=True):
    """Check the plan for errors. Returns list of dicts: {"group": int, "player_id": int|None, "reason": str}.

    instant_adds False (FFL_INSTANT_ADDS off): every free-agent target is an error, since Submit would add him."""
    errs = []

    def err(gi, pid, reason):
        errs.append({"group": gi, "player_id": pid, "reason": reason})

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
            elif t not in pool_ids:
                err(gi, t, "not in the free-agent pool (added by another team?)")
            if not instant_adds and status.get(t) == FREE:
                err(gi, t, INSTANT_OFF)

        # Cut validation.
        for c in g["cuts"]:
            if c in cuts:
                err(gi, c, f"already a cut in group {cuts[c] + 1}")
            cuts.setdefault(c, gi)
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


def decision_entry(groups, names, day, status):
    """Format a decision entry for journal/DECISIONS.md."""
    def nm(i):
        name = names.get(i, str(i))
        if status.get(i) == FREE:
            name += " (FA, added now)"
        return name

    def cut(i):
        return names.get(i, str(i))

    parts = []
    for i, g in enumerate(groups):
        targets_str = ", ".join(map(nm, g["targets"]))
        cuts_str = ", ".join(map(cut, g["cuts"])) or "none"
        cap = capacity(g)
        num_targets = len(g["targets"])
        part = f"({i + 1}) get {cap} of {num_targets}: {targets_str}; cut {cuts_str}"
        if g.get("open"):
            part += f" + {g['open']} open spot(s)"
        parts.append(part)

    header = f"## {day} — Waiver claims (from the app)"
    decision = "- **Decision:** " + "; ".join(parts)
    reasoning = "- **Reasoning:** (add by hand)"
    outcome = "- **Outcome (fill in later):**"

    return "\n".join([header, decision, reasoning, outcome, ""])


def matches(claims, pending):
    """Same (add, drop) sequence in the same order: ESPN runs claims by priority, so order is part of the plan."""
    return [(c["add"], c["drop"]) for c in claims] == [(c["add"], c["drop"]) for c in pending]


def run_submit(groups, status, pending, last_ids, replace_outside, writer):
    """Free agents first (instant, rank order, each on the group's next row; a refused add leaves its row for the next),
    then clear and repost the whole claim list over the rows left, so a retry after any failure is the same call. Stops
    the claims at the first failed write and still reads ESPN back: the page shows what ESPN holds, never what we hoped."""
    now, now_failed, used = [], [], {}
    for gi, g in enumerate(groups):
        r, n = rooms(g), 0
        for t in free_targets(g, status):
            if n >= len(r):
                break
            try:
                writer.add_free_agent(t, r[n])
                now.append({"group": gi, "add": t, "drop": r[n]})
                n += 1
            except Exception as e:  # noqa: BLE001 — taken or refused: his row goes to the next target
                now_failed.append({"group": gi, "add": t, "drop": r[n], "error": str(e)})
        used[gi] = n
    claims = expand(groups, status, used)
    sent, error = [], None
    try:
        for c in pending:
            if c["id"] in last_ids or replace_outside:
                writer.cancel_claim(c["id"])
        for c in claims:
            sent.append({**c, "id": writer.submit_claim(c["add"], c["drop"])})
    except Exception as e:  # noqa: BLE001 — reported in the result, with the read-back
        error = str(e)
    try:
        after, readback_failed = writer.pending_claims(), False
    except Exception as e:  # noqa: BLE001 — the writes landed; the caller must still journal them
        after, readback_failed = [], True
        error = error or f"read-back failed: {e}"
    # outside claims (filed on ESPN, not by this app) kept on purpose run ahead of ours: match ours, in order, without them
    kept_outside = set() if replace_outside else {c["id"] for c in pending if c["id"] not in last_ids}
    ours = [c for c in after if c["id"] not in kept_outside]
    return {"now": now, "now_failed": now_failed, "sent": sent, "pending": after,
            "match": error is None and matches(claims, ours), "error": error, "readback_failed": readback_failed}


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
        elif m["drop"] in g["cuts"]:
            g["cuts"].remove(m["drop"])
    return [g for g in out if g["targets"]]
