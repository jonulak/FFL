"""In-season maths, pure over plain dicts (same player shape the season box scores are flattened to).

Same shape as board.build / draft_watch.recommend: facts, no verdicts. Nothing here reads the
network, and every projection function is pure over its arguments, so every number the in-season
UI shows can be reproduced in a test. Two deliberate exceptions: is_live() reads the clock, and
append_decision() appends to journal/DECISIONS.md on disk.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .board import bench_depth, starter_counts
from .draft_watch import SEASON_WEEKS, _is_flex

# The order lineups are printed in — ESPN's own slot order, fixed positions before flex.
SLOT_ORDER = ["QB", "RB", "WR", "TE", "RB/WR/TE", "RB/WR", "WR/TE", "OP", "D/ST", "K"]
# Injury statuses that mean the player cannot score this week. QUESTIONABLE/DOUBTFUL are left
# playable on purpose: that call is his, on practice-report news the tree cannot see.
UNPLAYABLE = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
BENCH_SLOTS = {"BE", "IR"}
NFL_WEEKS = 18
# ESPN's fantasy status carries no return date; the public injuries feed does. These are the
# fallback for a player that feed has no date for: OUT is usually one game, IR a four-game minimum.
GAMES_OUT = {"OUT": 1, "INJURY_RESERVE": 4, "SUSPENSION": 4}
INJURY_FACTOR = {"QUESTIONABLE": 0.85, "DOUBTFUL": 0.7}
# STRATEGY.md, "Lineup": a starter within CLOSE_GAP points of his bench alternative, or a player
# whose two sources disagree by CLOSE_SPREAD or more, is a close call — flagged, not re-decided.
CLOSE_GAP = 2.0
CLOSE_SPREAD = 3.0
GROUP_ORDER = ["starter", "bench", "ir"]


def playable_proj(p: dict) -> float:
    """This week's projection, zeroed for anyone who cannot take the field."""
    if p.get("bye") or p.get("injury") in UNPLAYABLE:
        return 0.0
    return float(p.get("proj") or 0.0)


def _entry(slot: str, p: dict | None) -> dict:
    if p is None:
        return {"slot": slot, "player_id": None, "name": "—", "proj": 0.0}
    return {"slot": slot, "player_id": p["player_id"], "name": p["name"], "proj": playable_proj(p)}


def _slot_accepts(slot: str) -> set[str]:
    """Which positions a slot will take. "OP" is a superflex (QB included); "RB/WR/TE" is not."""
    if slot == "OP":
        return {"QB", "RB", "WR", "TE"}
    return set(slot.split("/")) if _is_flex(slot) else {slot}


def optimal_lineup(roster: list[dict], settings: dict) -> list[dict]:
    """Best lineup by projection. Fixed slots first (best of each position), then flex from what is
    left — optimal for slots whose eligibility is by position (exchange argument), which is every
    ESPN slot. IR players and OUT/bye players score 0 so they are never chosen over a live body."""
    pool = sorted((p for p in roster if p.get("slot") != "IR"), key=playable_proj, reverse=True)
    used: set[int] = set()
    out: list[dict] = []
    slots = [s for s in SLOT_ORDER if s in settings["starters"]]
    for phase in (False, True):  # fixed slots, then flex
        for slot in slots:
            if _is_flex(slot) != phase:
                continue
            accepts = _slot_accepts(slot)
            for _ in range(settings["starters"][slot]):
                pick = next((p for p in pool if p["player_id"] not in used and p["pos"] in accepts
                             and playable_proj(p) > 0), None)
                if pick is not None:
                    used.add(pick["player_id"])
                out.append(_entry(slot, pick))
    order = {s: i for i, s in enumerate(SLOT_ORDER)}
    return sorted(out, key=lambda e: order[e["slot"]])


def current_lineup(roster: list[dict]) -> list[dict]:
    """The lineup as it is actually set on ESPN right now."""
    order = {s: i for i, s in enumerate(SLOT_ORDER)}
    starters = [p for p in roster if p.get("slot") not in BENCH_SLOTS]
    return [_entry(p["slot"], p) for p in sorted(starters, key=lambda p: order.get(p["slot"], 99))]


def lineup_total(lineup: list[dict]) -> float:
    return round(sum(e["proj"] for e in lineup), 1)


def lineup_diff(current: list[dict], optimal: list[dict]) -> list[dict]:
    """Slot-by-slot swaps between what is set and what projection says. Slots are paired in order
    within each slot type, so two flex spots compare first-to-first, second-to-second."""
    cur_ids = {e["player_id"] for e in current}
    opt_ids = {e["player_id"] for e in optimal}
    swaps = []
    for slot in SLOT_ORDER:
        # a player who is already starting somewhere is not a swap, even if his slot moved
        cur = [e for e in current if e["slot"] == slot and e["player_id"] not in opt_ids]
        opt = [e for e in optimal if e["slot"] == slot and e["player_id"] not in cur_ids]
        for o, i in zip(cur, opt):
            swaps.append({"slot": slot, "out": o["name"], "in": i["name"],
                          "gain": round(i["proj"] - o["proj"], 1)})
        for i in opt[len(cur):]:  # an empty slot being filled: pure gain
            swaps.append({"slot": slot, "out": "—", "in": i["name"], "gain": round(i["proj"], 1)})
    return sorted(swaps, key=lambda d: -d["gain"])


POS_ORDER = ["QB", "RB", "WR", "TE", "D/ST", "K"]


# The flex slots as the card names them: ESPN calls the slot "RB/WR/TE", the manager calls it flex.
SLOT_LABEL = {"RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX"}


def slot_label(slot: str) -> str:
    return SLOT_LABEL.get(slot, slot)


def _card_sort_key(c: dict) -> tuple:
    """Section, then position in lineup order, then this week's projection, best first."""
    pos = POS_ORDER.index(c["pos"]) if c["pos"] in POS_ORDER else len(POS_ORDER)
    return (GROUP_ORDER.index(c["group"]), pos, -float(c.get("proj") or 0.0), c["name"])


def roster_cards(roster: list[dict], settings: dict, week: int) -> list[dict]:
    """STRATEGY.md, "Lineup": one card per rostered player with the call and its reason.

    The call is optimal_lineup's; the card adds the gap that decides it (starter vs the best bench
    alternative for his slot; bench player vs the weakest starter in a slot that takes him), a
    close-call flag on that gap or on the source spread, and a one-line reason from a template.
    QUESTIONABLE/DOUBTFUL keep the projection's call — the status is his call, and the line says so.
    """
    optimal = optimal_lineup(roster, settings)
    slot_of = {e["player_id"]: e["slot"] for e in optimal if e["player_id"] is not None}
    by_id = {p["player_id"]: p for p in roster}
    bench = [p for p in roster if p["player_id"] not in slot_of and p.get("slot") != "IR" and playable_proj(p) > 0]
    cards = []
    for p in roster:
        gap: float | None = None
        if p.get("slot") == "IR":
            group, call, reason = "ir", "IR", "IR slot"
        elif p.get("bye"):
            group, call, reason = "bench", "BYE", "bye week"
        elif p.get("injury") in UNPLAYABLE:
            group, call, reason = "bench", "OUT", f"out: {p['injury']}"
        elif p["player_id"] in slot_of:
            group, call = "starter", "START"
            slot = slot_of[p["player_id"]]
            alt = max((b for b in bench if b["pos"] in _slot_accepts(slot)), key=playable_proj, default=None)
            if alt is None:
                reason = f"starts at {slot_label(slot)}: no bench alternative"
            else:
                gap = round(playable_proj(p) - playable_proj(alt), 1)
                reason = f"starts at {slot_label(slot)}: {playable_proj(p):.1f} vs {playable_proj(alt):.1f} for {alt['name']}"
        else:
            group, call = "bench", "BENCH"
            takers = [(by_id[e["player_id"]], e["slot"]) for e in optimal
                      if e["player_id"] is not None and p["pos"] in _slot_accepts(e["slot"])]
            weakest = min(takers, key=lambda t: playable_proj(t[0]), default=None)
            if weakest is None:
                reason = f"bench: no slot takes a {p['pos']}"
            else:
                gap = round(playable_proj(p) - playable_proj(weakest[0]), 1)
                reason = f"bench: {abs(gap):.1f} behind {weakest[0]['name']} at {slot_label(weakest[1])}"
        close = call in ("START", "BENCH") and (
            (gap is not None and abs(gap) < CLOSE_GAP) or float(p.get("spread") or 0.0) >= CLOSE_SPREAD)
        if call in ("START", "BENCH") and p.get("injury") in INJURY_FACTOR:
            reason += " — his status is your call"
        cards.append({**p, "group": group, "call": call, "gap": gap, "close_call": close, "reason": reason})
    return sorted(cards, key=_card_sort_key)


def ros_fraction(week: int) -> float:
    return min(max((NFL_WEEKS - week) / SEASON_WEEKS, 0.0), 1.0)


def ros_from_sources(p: dict) -> bool:
    """True when espn_season's lookup found him in Sleeper's weekly rows or CBS's rest-of-season page."""
    return p.get("ros_proj") is not None


def ros_proj(p: dict, week: int) -> float:
    """His rest-of-season points (STRATEGY.md, "Waivers"): the sources' number when either has him,
    else the season projection scaled by the weeks left."""
    if ros_from_sources(p):
        return float(p["ros_proj"])
    return round(float(p.get("season_proj") or 0.0) * ros_fraction(week), 1)


def week_of(d: date, week1_start: date) -> int:
    """The fantasy week a date falls in; week 1 is the seven days from week1_start."""
    return (d - week1_start).days // 7 + 1


def games_out(return_week: int | None, week: int) -> int | None:
    """Games he still misses, counted from this week. None when no return week is known — and when
    the reported week has already passed while the status still says OUT/IR, the date is stale."""
    return None if return_week is None or return_week <= week else return_week - week


def injury_terms(injury: str, week: int, games_out: int | None = None) -> tuple[float, float]:
    """(multiplier, share of the remaining games he can play)."""
    if injury in GAMES_OUT:
        left = NFL_WEEKS - week
        missed = games_out if games_out is not None else GAMES_OUT[injury]
        avail = max(left - missed, 0) / left if left > 0 else 0.0
        return 1.0, avail
    return INJURY_FACTOR.get(injury or "", 1.0), 1.0


def add_score(ros_delta: float, week_delta: float, injury: str, week: int,
              games_out: int | None = None, injury_priced: bool = False) -> float:
    """STRATEGY.md, "Waivers": add = (ros_delta + week_delta if he would start) × injury factor;
    OUT/IR get no week term and, unless the ROS sources already left out the games he misses
    (`injury_priced`), count only the games after return."""
    factor, avail = injury_terms(injury, week, games_out)
    if injury in GAMES_OUT:
        return round(ros_delta * (1.0 if injury_priced else avail), 1)
    return round((ros_delta + week_delta) * factor, 1)


def weakest_rostered(my_roster: list[dict], pos: str, week: int) -> float:
    mine = [ros_proj(p, week) for p in my_roster if p["pos"] == pos and p.get("slot") != "IR"]
    return min(mine) if mine else 0.0


def pool_replacement(pool: list[dict], settings: dict, pos: str, week: int) -> float:
    """The last player at `pos` who still gets rostered league-wide, by rest-of-season projection —
    the same demand count board.build uses (starters + bench depth), on the rostered+free pool."""
    demand = int(round(starter_counts(settings)[pos] + bench_depth(settings)[pos]))
    ranked = sorted((ros_proj(p, week) for p in pool if p["pos"] == pos), reverse=True)
    if not ranked:
        return 0.0
    idx = min(max(demand - 1, 0), len(ranked) - 1)
    return ranked[idx]


def waiver_deltas(free_agents: list[dict], my_roster: list[dict], pool: list[dict],
                  settings: dict, week: int) -> list[dict]:
    """Per free agent: ROS projection against my weakest at his position and against the pool's
    replacement level, whether he would start this week and for how much, and `add`."""
    current = optimal_lineup(my_roster, settings)
    cur_total = lineup_total(current)
    rows = []
    for fa in free_agents:
        pos = fa["pos"]
        ros = ros_proj(fa, week)
        d_roster = round(ros - weakest_rostered(my_roster, pos, week), 1)
        d_pool = round(ros - pool_replacement(pool, settings, pos, week), 1)
        with_him = optimal_lineup(my_roster + [{**fa, "slot": "BE"}], settings)
        week_delta = round(lineup_total(with_him) - cur_total, 1)
        would_start = week_delta > 0
        ros_delta = min(d_roster, d_pool)
        rows.append({**fa, "ros_proj": ros, "week_proj": playable_proj(fa), "delta_roster": d_roster,
                     "delta_pool": d_pool, "would_start": would_start,
                     "week_delta": week_delta if would_start else 0.0,
                     "add": add_score(ros_delta, week_delta if would_start else 0.0, fa.get("injury"), week,
                                      games_out(fa.get("return_week"), week), ros_from_sources(fa))})
    return sorted(rows, key=lambda r: -r["add"])


# STRATEGY.md, "Recent form": the window is his last FORM_GAMES games played, looked for within the
# last FORM_WEEKS weeks (which bounds the per-week ESPN calls the pool builder makes).
FORM_GAMES = 3
FORM_WEEKS = 5
SNAP_POSITIONS = {"QB", "RB", "WR", "TE"}
TARGET_POSITIONS = {"RB", "WR", "TE"}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def games_left(week: int, bye_week: int | None) -> int:
    """Games from `week` to the end of the schedule, his bye taken out when it is still ahead —
    the same span Sleeper's remaining weeks sum over, so pace and ROS are the same unit."""
    left = max(NFL_WEEKS - week + 1, 0)
    if bye_week is not None and week <= bye_week <= NFL_WEEKS:
        left -= 1
    return left


def recent_form(games: list[dict], pos: str, ros: float, games_left: int) -> dict | None:
    """STRATEGY.md, "Recent form", over one player's weekly game rows (espn_season.game_from_stats).

    A game is in the window when it is final and he was active in it (an opportunity, or any stat
    line for a D/ST); the last FORM_GAMES of those, in week order. Fewer than that carries its
    count; none at all is None, so the page shows nothing rather than a zero. Snap columns cover
    only the games Sleeper has snaps for (they land a day after the games) and carry that count.
    """
    window = sorted((g for g in games if g["final"] and g["active"]), key=lambda g: g["week"])[-FORM_GAMES:]
    if not window:
        return None
    pts = [float(g["pts"]) for g in window]
    l3 = _mean(pts)
    opps = [float(g["opp"]) for g in window if g.get("opp") is not None]
    opp = _mean(opps) if opps and pos != "D/ST" else None
    trend = round(opps[-1] - _mean(opps[:-1]), 1) if opp is not None and len(opps) >= 2 else None
    snapped = [g for g in window if g.get("snaps") is not None and g.get("team_snaps")] if pos in SNAP_POSITIONS else []
    snaps, team_snaps = sum(float(g["snaps"]) for g in snapped), sum(float(g["team_snaps"]) for g in snapped)
    tgts = sum(float(g.get("tgt") or 0) for g in window)
    yds = sum(float(g.get("rec_yd") or 0) for g in window)
    targets = pos in TARGET_POSITIONS
    return {"games": len(window), "weeks": [g["week"] for g in window],
            "l3": round(l3, 1),
            "beat": round(_mean([float(g["pts"]) - float(g["proj"]) for g in window]), 1),
            "opp": round(opp, 1) if opp is not None else None, "opp_trend": trend,
            "snap_pct": round(100 * snaps / team_snaps) if snapped and team_snaps > 0 else None,
            "snap_games": len(snapped),
            "tgt_snap": round(sum(float(g.get("tgt") or 0) for g in snapped) / snaps, 2) if targets and snapped and snaps > 0 else None,
            "ypt": round(yds / tgts, 1) if targets and tgts > 0 else None,
            "pace_gap": round(l3 * games_left - float(ros), 1)}


def append_decision(path: Path, title: str, decision: str, reasoning: str, alternatives: str,
                    today: date | None = None) -> str:
    """Append one DECISIONS.md entry in the journal's format. Never creates the file."""
    if not all(s and s.strip() for s in (title, decision, reasoning)):
        raise ValueError("title, decision and reasoning are required")
    if not path.exists():
        raise FileNotFoundError(path)
    day = (today or date.today()).isoformat()
    block = (f"\n## {day} — {title.strip()}\n- **Decision:** {decision.strip()}\n"
             f"- **Reasoning:** {reasoning.strip()}\n- **Alternatives rejected:** {alternatives.strip()}\n"
             f"- **Outcome (fill in later):**\n")
    with path.open("a") as f:
        f.write(block)
        f.flush()
        os.fsync(f.fileno())
    return path.read_text()


def is_live(now: datetime | None = None) -> bool:
    """Games are on: Thursday 8 PM ET through Monday night (2 AM Tuesday)."""
    ny = ZoneInfo("America/New_York")
    t = (now or datetime.now(ny)).astimezone(ny)
    wd, h = t.weekday(), t.hour  # Mon=0
    if wd == 3:
        return h >= 20
    if wd in (4, 5, 6, 0):
        return True
    if wd == 1:
        return h < 2
    return False
