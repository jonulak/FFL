"""In-season maths, pure over plain dicts (same player shape the season box scores are flattened to).

Same shape as board.build / draft_watch.recommend: facts, no verdicts. Nothing here reads the
network, and every projection function is pure over its arguments, so every number the in-season
UI shows can be reproduced in a test. One deliberate exception: is_live() reads the clock.
"""
from __future__ import annotations

import math
import unicodedata
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from . import decisions
from .board import bench_depth, starter_counts
from .draft_watch import SEASON_WEEKS, _is_flex

# The order lineups are printed in — ESPN's own slot order, fixed positions before flex.
SLOT_ORDER = ["QB", "RB", "WR", "TE", "RB/WR/TE", "RB/WR", "WR/TE", "OP", "D/ST", "K"]
# Injury statuses that mean the player cannot score this week. QUESTIONABLE/DOUBTFUL are left
# playable on purpose: that call is his, on practice-report news the tree cannot see.
UNPLAYABLE = {"OUT", "INJURY_RESERVE", "SUSPENSION"}
BENCH_SLOTS = {"BE", "IR"}
# The league's IR rule (Jonathan, 2026-10-07): only a player ESPN lists IR or Out may go INTO the IR slot; one
# already there may STAY while he carries any designation (Q, D, Out, IR, suspension). With none (ACTIVE, NORMAL,
# blank) the roster is illegal and ESPN allows no other move until he comes off IR. web/lib/lineup.ts mirrors these.
IR_ENTRY = {"INJURY_RESERVE", "OUT"}
NO_DESIGNATION = {"ACTIVE", "NORMAL", "", None}
# ESPN's default when an older settings.json predates `ir_size` (this league has 1)
IR_SIZE_DEFAULT = 1
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


# Win probability: context beside the call, never an input to it. The spread of one full unplayed
# game by position; ffl.calibrate fits the real ones into the DB's sigma_fits and these
# stand in for any position the file lacks.
SIGMA_FALLBACK = {"QB": 7.5, "RB": 7.0, "WR": 7.0, "TE": 5.5, "K": 4.0, "D/ST": 5.5}
SIGMA_DEFAULT = 6.0


def _clock_minutes(clock) -> float:
    try:
        m, s = str(clock).split(":")
        return int(m) + int(s) / 60
    except (ValueError, AttributeError):
        return 0.0


def game_left(p: dict) -> float:
    """The share of his game still to play: off the live feed's quarter and clock while it has his
    game in progress (overtime counts as none left), else 0 once the feed or ESPN's game_played
    calls it over, else the whole game. The feed wins over game_played, as in matchup_final."""
    g = p.get("game") or {}
    if g.get("status") == "in":
        q = g.get("quarter")
        if not q:
            return 1.0
        if q > 4:
            return 0.0
        return max(0.0, min(1.0, (4 - q + _clock_minutes(g.get("clock")) / 15) / 4))
    if g.get("status") == "post" or int(p.get("game_played") or 0) >= 100:
        return 0.0
    return 1.0


def side_outlook(lineup: list[dict], sigma: dict[str, float]) -> tuple[float, float]:
    """One side's starters as (expected final, variance): posted points plus each playable
    projection scaled by the game left, each adding its position's spread over that share."""
    mean = var = 0.0
    for p in lineup:
        actual, proj, left = float(p.get("actual") or 0), playable_proj(p), game_left(p)
        if left == 1.0 and actual > 0 and not p.get("game"):
            # points posted but no clock to read (the feed is down): what is left of his
            # projection is still to come, the board's own rule (web/lib/game.ts sideTotals)
            left = max(0.0, proj - actual) / proj if proj > 0 else 0.0
        mean += actual + proj * left
        if proj > 0:
            sd = sigma.get(p.get("pos"), SIGMA_FALLBACK.get(p.get("pos"), SIGMA_DEFAULT))
            var += sd * sd * left
    return mean, var


def win_prob(a: list[dict], b: list[dict], sigma: dict[str, float]) -> float:
    """P(side a outscores side b), the margin read as normal; with nothing left to play, the score."""
    ma, va = side_outlook(a, sigma)
    mb, vb = side_outlook(b, sigma)
    if va + vb <= 0:
        # the score as the board shows it, so float noise in the sums never splits a tie
        ma, mb = round(ma, 2), round(mb, 2)
        return 1.0 if ma > mb else 0.0 if ma < mb else 0.5
    return 0.5 * (1 + math.erf((ma - mb) / math.sqrt(2 * (va + vb))))


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
    ESPN slot. IR players and OUT/bye players score 0 so they are never chosen over a live body; a player sat by
    hand for the week (`sat`) is left out like IR. A player whose game has started (`locked`) cannot move: a starter
    keeps his slot whatever his projection, a bench player cannot start."""
    slots = [s for s in SLOT_ORDER if s in settings["starters"]]
    held = [p for p in roster if p.get("locked") and p.get("slot") in slots]
    open_n = Counter(settings["starters"])
    open_n.subtract(Counter(p["slot"] for p in held))
    pool = sorted((p for p in roster if p.get("slot") != "IR" and not p.get("sat") and not p.get("locked")),
                  key=playable_proj, reverse=True)
    used: set[int] = set()
    out: list[dict] = [_entry(p["slot"], p) for p in held]
    for phase in (False, True):  # fixed slots, then flex
        for slot in slots:
            if _is_flex(slot) != phase:
                continue
            accepts = _slot_accepts(slot)
            for _ in range(max(open_n[slot], 0)):
                pick = next((p for p in pool if p["player_id"] not in used and p["pos"] in accepts
                             and playable_proj(p) > 0), None)
                if pick is not None:
                    used.add(pick["player_id"])
                out.append(_entry(slot, pick))
    out = _place_by_kickoff(out, {p["player_id"]: p for p in [*held, *pool]})
    order = {s: i for i, s in enumerate(SLOT_ORDER)}
    return sorted(out, key=lambda e: order[e["slot"]])


def _kick_key(p: dict) -> float:
    k = p.get("kickoff")
    return datetime.fromisoformat(k).timestamp() if k else float("-inf")


def _place_by_kickoff(lineup: list[dict], by_id: dict[int, dict]) -> list[dict]:
    """STRATEGY.md "Slot by kickoff": the same starters re-placed so the latest games hold the flex spots. A started
    game keeps the slot ESPN has him in; then, earliest kickoff first, each takes his position's own slot while one is
    open, else the narrowest flex that takes him. Ties keep projection order. Empty slots stay as they are."""
    filled = [e for e in lineup if e["player_id"] is not None]
    open_slots = [e["slot"] for e in filled]
    placed: list[dict] = []
    rest = []
    for e in filled:
        p = by_id[e["player_id"]]
        if p.get("locked") and p.get("slot") in open_slots and p["pos"] in _slot_accepts(p["slot"]):
            open_slots.remove(p["slot"])
            placed.append(_entry(p["slot"], p))
        else:
            rest.append(p)
    for p in sorted(rest, key=_kick_key):
        fits = [s for s in open_slots if p["pos"] in _slot_accepts(s)]
        if not fits:
            return lineup  # cannot happen for a feasible lineup; keep projection's placement rather than drop him
        slot = min(fits, key=lambda s: (_is_flex(s), len(_slot_accepts(s))))
        open_slots.remove(slot)
        placed.append(_entry(slot, p))
    return placed + [e for e in lineup if e["player_id"] is None]


def current_lineup(roster: list[dict]) -> list[dict]:
    """The lineup as it is actually set on ESPN right now."""
    order = {s: i for i, s in enumerate(SLOT_ORDER)}
    starters = [p for p in roster if p.get("slot") not in BENCH_SLOTS]
    return [_entry(p["slot"], p) for p in sorted(starters, key=lambda p: order.get(p["slot"], 99))]


def lineup_total(lineup: list[dict]) -> float:
    return round(sum(e["proj"] for e in lineup), 2)


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


def _group(p: dict) -> str:
    """The section ESPN has him in: its lineup, not the call."""
    s = p.get("slot")
    return "ir" if s == "IR" else "bench" if s in BENCH_SLOTS or s is None else "starter"


def _card_sort_key(c: dict) -> tuple:
    """Section; starters in ESPN's slot order and, inside a slot, ESPN's own order (`espn_order`, as its Matchup tab
    and ours list them); the rest by position, then this week's projection, best first."""
    if c["group"] == "starter":
        rank = SLOT_ORDER.index(c["slot"]) if c["slot"] in SLOT_ORDER else len(SLOT_ORDER)
        return (GROUP_ORDER.index(c["group"]), rank, c["espn_order"])
    else:
        rank = POS_ORDER.index(c["pos"]) if c["pos"] in POS_ORDER else len(POS_ORDER)
    return (GROUP_ORDER.index(c["group"]), rank, -float(c.get("proj") or 0.0), c["name"])


def ir_size(settings: dict) -> int:
    return int(settings.get("ir_size", IR_SIZE_DEFAULT))


def ir_illegal(roster: list[dict]) -> list[dict]:
    """The players on IR with no injury designation: while there are any, ESPN refuses every other move."""
    return [p for p in roster if p.get("slot") == "IR" and p.get("injury") in NO_DESIGNATION]


def position_caps(roster: list[dict], limits: dict) -> list[dict]:
    """ESPN's per-position roster caps (settings `position_limits`) with how many of each I hold, IR included: an
    add over a cap is refused. Empty when settings.json predates the key (re-run `ffl.settings`)."""
    held = Counter(p.get("pos") for p in roster)
    return [{"pos": pos, "n": held.get(pos, 0), "limit": cap} for pos, cap in limits.items()]


def empty_slots(roster: list[dict], settings: dict) -> list[str]:
    """One entry per starter spot ESPN has nobody in, in slot order, then one per open IR spot."""
    filled = Counter(p.get("slot") for p in roster)
    return ([s for s in SLOT_ORDER for _ in range(settings["starters"].get(s, 0) - filled.get(s, 0))]
            + ["IR"] * (ir_size(settings) - filled.get("IR", 0)))


def roster_cards(roster: list[dict], settings: dict, week: int) -> list[dict]:
    """STRATEGY.md, "Lineup": one card per rostered player with the call and its reason, grouped by
    ESPN's slot; the call is optimal_lineup's and shows as the chip.

    The call is optimal_lineup's; the card adds the gap that decides it (starter vs the best bench
    alternative for his slot; bench player vs the weakest starter in a slot that takes him), a
    close-call flag on that gap or on the source spread, and a one-line reason from a template.
    QUESTIONABLE/DOUBTFUL keep the projection's call — the status is his call, and the line says so.

    A player sat by hand (`sat`) is left out of the call: SIT, worth nothing to the lineup (`play_proj` 0, so the
    swap sheet never offers him), and the rest of the roster is called without him. `model_call` is the call he
    would have had, which the outlook's hash and sheet read so a sit does not rewrite his outlook.
    """
    model = ({c["player_id"]: c for c in roster_cards([{**p, "sat": False} for p in roster], settings, week)}
             if any(p.get("sat") for p in roster) else {})
    optimal = optimal_lineup(roster, settings)
    slot_of = {e["player_id"]: e["slot"] for e in optimal if e["player_id"] is not None}
    by_id = {p["player_id"]: p for p in roster}
    # who could still come in: a benched player whose game has started cannot
    bench = [p for p in roster if p["player_id"] not in slot_of and p.get("slot") != "IR" and not p.get("sat")
             and not p.get("locked") and playable_proj(p) > 0]
    cards = []
    for i, p in enumerate(roster):
        gap: float | None = None
        sat = bool(p.get("sat"))
        if p.get("slot") == "IR":
            call, reason = "IR", "IR slot"
        elif p.get("bye"):
            call, reason = "BYE", "bye week"
        elif p.get("injury") in UNPLAYABLE:
            call, reason = "OUT", f"out: {p['injury']}"
        elif sat:
            call, reason = "SIT", f"sat by hand this week — the call: {model[p['player_id']]['reason']}"
        elif p["player_id"] in slot_of:
            call = "START"
            slot = slot_of[p["player_id"]]
            alt = None if p.get("locked") else max((b for b in bench if b["pos"] in _slot_accepts(slot)), key=playable_proj, default=None)
            if p.get("locked"):
                reason = f"starts at {slot_label(slot)}: his game has started"
            elif alt is None:
                reason = f"starts at {slot_label(slot)}: no bench alternative"
            else:
                gap = round(playable_proj(p) - playable_proj(alt), 1)
                reason = f"starts at {slot_label(slot)}: {playable_proj(p):.1f} vs {playable_proj(alt):.1f} for {alt['name']}"
        else:
            call = "BENCH"
            takers = [(by_id[e["player_id"]], e["slot"]) for e in optimal if e["player_id"] is not None
                      and p["pos"] in _slot_accepts(e["slot"]) and not by_id[e["player_id"]].get("locked")]
            weakest = min(takers, key=lambda t: playable_proj(t[0]), default=None)
            if p.get("locked"):
                reason = "bench: his game has started"
            elif weakest is None:
                reason = f"bench: no open slot takes a {p['pos']}"
            else:
                gap = round(playable_proj(p) - playable_proj(weakest[0]), 1)
                reason = f"bench: {abs(gap):.1f} behind {weakest[0]['name']} at {slot_label(weakest[1])}"
        close = call in ("START", "BENCH") and (
            (gap is not None and abs(gap) < CLOSE_GAP) or float(p.get("spread") or 0.0) >= CLOSE_SPREAD)
        if call in ("START", "BENCH") and p.get("injury") in INJURY_FACTOR:
            reason += " — his status is your call"
        cards.append({**p, "group": _group(p), "call": call, "gap": gap, "close_call": close, "reason": reason,
                      "sat": sat, "model_call": model[p["player_id"]]["call"] if sat else call,
                      "ros_adj": ros_adj(p, week), "play_proj": 0.0 if sat else playable_proj(p),
                      "locked": bool(p.get("locked")),
                      "espn_order": p.get("espn_order", i),
                      # the slot a START puts him in (STRATEGY.md "Slot by kickoff"); the chip names it
                      "call_slot": slot_of.get(p["player_id"]) if call == "START" else None})
    return sorted(cards, key=_card_sort_key)


def ros_fraction(week: int) -> float:
    return min(max((NFL_WEEKS - week) / SEASON_WEEKS, 0.0), 1.0)


def ros_from_sources(p: dict) -> bool:
    """True when espn_season's lookup found him in Sleeper's weekly rows or CBS's rest-of-season page."""
    return p.get("ros_proj") is not None


def ros_proj(p: dict, week: int) -> float:
    """His rest-of-season points by the models (STRATEGY.md, "Waivers"): the sources' number when any
    has him (already net of the games he misses), else the season projection scaled by the weeks left
    and, OUT/IR, counting only the games from his return week. The haircut lives here, on ROS, so
    every delta runs on it: scaling a delta instead pulled a negative one toward zero (ffl-piy)."""
    if ros_from_sources(p):
        return float(p["ros_proj"])
    _, avail = injury_terms(p.get("injury") or "", week, games_out(p.get("return_week"), week))
    return round(float(p.get("season_proj") or 0.0) * ros_fraction(week) * avail, 1) + 0.0


# STRATEGY.md, "Usage-adjusted ROS": the prior is worth K starter-share games. A guess about noise,
# checked by ffl.calibrate against the weekly snapshots — change it there, not by eye.
K_PRIOR = 4


def ros_adj(p: dict, week: int) -> float:
    """His rest-of-season points as `add` runs on them: the model's per-game rate shrunk toward his
    recent games by the window's snap-weighted game count (`form["weight"]`), over the games he can
    still play. The model as it stands when he has no games, no weight, or nothing left to play."""
    model = ros_proj(p, week)
    f = p.get("form")
    if not f or not f.get("weight"):
        return model
    left = int(f.get("games_left") or 0)
    if (p.get("injury") or "") in GAMES_OUT:
        missed = games_out(p.get("return_week"), week)
        left -= missed if missed is not None else GAMES_OUT[p["injury"]]
    if left <= 0:
        return model
    w = float(f["weight"])
    return round(left * (w * float(f["l3"]) + K_PRIOR * model / left) / (w + K_PRIOR), 1)


# Every kickoff in the week this long ago means the week's games are all played.
GAME_HOURS = 4


def ros_week(week: int, kickoffs: list[datetime], now: datetime | None = None) -> int:
    """The week rest-of-season starts from: ESPN's scoring period, or the next one once every
    kickoff in it is GAME_HOURS old — the period rolls some time Tuesday, and until then the
    Sleeper sum counts a game already played. An empty schedule leaves ESPN's week alone."""
    if not kickoffs or week >= NFL_WEEKS:
        return week
    t = now or datetime.now(ZoneInfo("America/New_York"))
    last = max(kickoffs)
    return week + 1 if (t - last).total_seconds() >= GAME_HOURS * 3600 else week


# Inactives are posted 90 minutes before kickoff; the notify tick (every 15 min) pulls fresh projections and
# injuries at its first run inside this many minutes of a kickoff, so the pull lands 70-85 minutes out.
PREGAME_PULL_MIN = 85


def pregame_due(kickoffs: list[datetime], now: datetime, pulled: set[str]) -> list[datetime]:
    """The kickoffs whose pre-game pull is due: inside PREGAME_PULL_MIN of the ball, not yet kicked, not yet pulled
    (`pulled` holds isoformat kickoffs). Games sharing a kickoff are one pull."""
    lead = PREGAME_PULL_MIN * 60
    return sorted({k for k in kickoffs if 0 < (k - now).total_seconds() <= lead and k.isoformat() not in pulled})


def settle_at(last_kickoff: datetime) -> datetime:
    """When a finished week's points are final: the end of the first Thursday (ET) after its last kickoff, so a
    Monday-night week settles Friday 00:00 ET and the correction pass's Tuesday-Thursday reads catch ESPN's stat
    corrections (ffl-7sq). The day is the ET date: a Monday 8:15 pm kickoff is Tuesday in UTC."""
    ny = ZoneInfo("America/New_York")
    d = last_kickoff.astimezone(ny).date()
    days = (3 - d.weekday()) % 7 or 7  # to the next Thursday; a Thursday's own week waits for the one after
    return datetime.combine(d + timedelta(days=days + 1), dtime(0), tzinfo=ny)


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


def add_score(ros_delta: float, week_delta: float, injury: str) -> float:
    """STRATEGY.md, "Waivers": add = (ros_delta + week_delta if he would start) × injury factor;
    OUT/IR get no week term and no factor (his ROS already counts only the games after return,
    see `ros_proj`). Never scale a delta: it cannot tell a worthless player from a neutral one."""
    if injury in GAMES_OUT:
        return round(ros_delta, 1) + 0.0
    return round((ros_delta + week_delta) * INJURY_FACTOR.get(injury or "", 1.0), 1) + 0.0


def weakest_rostered(my_roster: list[dict], pos: str, week: int) -> float:
    mine = [ros_adj(p, week) for p in my_roster if p["pos"] == pos and p.get("slot") != "IR"]
    return min(mine) if mine else 0.0


def pool_replacement(pool: list[dict], settings: dict, pos: str, week: int) -> float:
    """The last player at `pos` who still gets rostered league-wide, by usage-adjusted ROS —
    the same demand count board.build uses (starters + bench depth), on the rostered+free pool."""
    demand = int(round(starter_counts(settings)[pos] + bench_depth(settings)[pos]))
    ranked = sorted((ros_adj(p, week) for p in pool if p["pos"] == pos), reverse=True)
    if not ranked:
        return 0.0
    idx = min(max(demand - 1, 0), len(ranked) - 1)
    return ranked[idx]


def waiver_deltas(free_agents: list[dict], my_roster: list[dict], pool: list[dict],
                  settings: dict, week: int, week_over: bool = False) -> list[dict]:
    """Per free agent: usage-adjusted ROS (`ros_adj`; the model mean stays in `ros_proj`) against
    my weakest at his position and against the pool's replacement level, whether he would start
    this week and for how much (nothing, once the week's games are all played), and `add`.
    `week` is the ROS week (season.ros_week)."""
    current = optimal_lineup(my_roster, settings)
    cur_total = lineup_total(current)
    rows = []
    for fa in free_agents:
        pos = fa["pos"]
        ros = ros_adj(fa, week)
        d_roster = round(ros - weakest_rostered(my_roster, pos, week), 1)
        d_pool = round(ros - pool_replacement(pool, settings, pos, week), 1)
        with_him = optimal_lineup(my_roster + [{**fa, "slot": "BE"}], settings)
        week_delta = 0.0 if week_over else round(lineup_total(with_him) - cur_total, 1)
        would_start = week_delta > 0
        ros_delta = min(d_roster, d_pool)
        # status/waiver_date are ESPN's claim facts, filled in by espn_season; unknown counts as WAIVERS
        rows.append({**fa, "status": None, "waiver_date": None,
                     "ros_proj": ros_proj(fa, week), "ros_adj": ros, "week_proj": playable_proj(fa),
                     "delta_roster": d_roster, "delta_pool": d_pool, "would_start": would_start,
                     "week_delta": week_delta if would_start else 0.0,
                     "add": add_score(ros_delta, week_delta if would_start else 0.0, fa.get("injury"))})
    return sorted(rows, key=lambda r: -r["add"])


# STRATEGY.md, "Recent form": the window is his last FORM_GAMES games played, looked for within the
# last FORM_WEEKS weeks (which bounds the per-week ESPN calls the pool builder makes).
FORM_GAMES = 3
FORM_WEEKS = 5
SNAP_POSITIONS = {"QB", "RB", "WR", "TE"}
TARGET_POSITIONS = {"RB", "WR", "TE"}


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# STRATEGY.md, "Usage-adjusted ROS": the league's lead players at a position, by snaps — one RB,
# two WR, one TE and one QB per club. Their median snap share is what a game is weighed against.
LEAD_PLAYERS = {"QB": 32, "RB": 32, "WR": 64, "TE": 32}


def snap_reference(snaps: dict[tuple[str, str], tuple[float, float]]) -> dict[str, float]:
    """The median snap share of the lead players at each position, for one week, over Sleeper's
    rows ((name, pos) -> (snaps, team snaps), as espn_season.parse_sleeper_snaps keys them). A
    position nobody has snaps for is absent, and a game that week weighs one."""
    shares: dict[str, list[float]] = {}
    for (_, pos), (s, t) in snaps.items():
        if pos in LEAD_PLAYERS and t > 0:
            shares.setdefault(pos, []).append(s / t)
    return {pos: round(_median(sorted(v, reverse=True)[:LEAD_PLAYERS[pos]]), 3) for pos, v in shares.items()}


def _game_weight(g: dict) -> float:
    """What one game in the window is worth to the blend: his snap share against the position's
    reference that week, capped at one; a full game when either is missing."""
    ref, snaps, team = g.get("ref"), g.get("snaps"), g.get("team_snaps")
    if not ref or snaps is None or not team:
        return 1.0
    return min(1.0, (float(snaps) / float(team)) / float(ref))


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
    only the games Sleeper has snaps for (they land a day after the games) and carry that count;
    air yards and red-zone looks come off the same rows and carry theirs (`air_games`).
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
    # air yards and red-zone looks, over the games Sleeper's row has landed for (with the snaps)
    aired = [g for g in window if g.get("air_yd") is not None] if pos in SNAP_POSITIONS else []
    tot = lambda k: sum(float(g.get(k) or 0) for g in aired)  # noqa: E731
    per_game = lambda k: round(tot(k) / len(aired), 1) if aired else None  # noqa: E731
    team_air = sum(float(g["team_air"]) for g in aired if g.get("team_air"))
    catches = tot("cmp") if pos == "QB" else tot("catches")  # Sleeper's own, like the air yards
    air = tot("pass_air") if pos == "QB" else tot("air_yd")
    # the window game by game, so the outlook can read the trend's shape and not just its mean
    rows = [{"week": g["week"], "pts": round(float(g["pts"]), 2),
             "snap_pct": round(100 * float(g["snaps"]) / float(g["team_snaps"]))
             if g.get("snaps") is not None and g.get("team_snaps") else None,
             "tgt": g.get("tgt"), "car": g.get("car"), "rec": g.get("rec"), "pass_att": g.get("pass_att"),
             "air_yd": g.get("pass_air") if pos == "QB" else g.get("air_yd"),
             "rz_tgt": g.get("rz_tgt"), "rz_att": g.get("rz_att"), "rz_pass": g.get("rz_pass")}
            for g in window]
    return {"games": len(window), "weeks": [g["week"] for g in window],
            "l3": round(l3, 2),
            "beat": round(_mean([float(g["pts"]) - float(g["proj"]) for g in window]), 2),
            "opp": round(opp, 1) if opp is not None else None, "opp_trend": trend,
            "snap_pct": round(100 * snaps / team_snaps) if snapped and team_snaps > 0 else None,
            "snap_games": len(snapped),
            "tgt_snap": round(sum(float(g.get("tgt") or 0) for g in snapped) / snaps, 2) if targets and snapped and snaps > 0 else None,
            "ypt": round(yds / tgts, 1) if targets and tgts > 0 else None,
            # completed air yards (Sleeper has no intended ones): a receiver's share of his club's, air
            # yards per catch (per completion for a QB), and red-zone targets / rushes / pass attempts
            # per game, over `air_games`
            "air_share": round(100 * air / team_air) if targets and aired and team_air > 0 else None,
            "air_catch": round(air / catches, 1) if aired and catches > 0 else None,
            "rz_tgt": per_game("rz_tgt") if targets else None,
            "rz_att": per_game("rz_att") if pos in ("QB", "RB") else None,
            "rz_pass": per_game("rz_pass") if pos == "QB" else None,
            "air_games": len(aired),
            "pace_gap": round(l3 * games_left - float(ros), 1),
            # what the window is worth to the usage-adjusted ROS, and the span it is paced over
            "weight": round(sum(_game_weight(g) for g in window), 2), "games_left": games_left,
            "rows": rows}


def roster_rows(my_roster: list[dict], week: int) -> list[dict]:
    """My players as the Waivers page compares a free agent against them: the same rest-of-season
    and week figures a waiver row carries (`ros_proj` resolved, `week_proj` playable), ordered by
    ROS. IR players stay in: a drop is a drop."""
    rows = [{**p, "droppable": True, "locked": False,  # ESPN's flags, filled in by espn_season
             "ros_proj": ros_proj(p, week), "ros_adj": ros_adj(p, week), "week_proj": playable_proj(p)}
            for p in my_roster]
    return sorted(rows, key=lambda r: -r["ros_adj"])


TX_KIND = {"WAIVER ADDED": "waiver", "FA ADDED": "free_agent"}


def parse_tx_id(tx_id: str) -> tuple[str, int, str, int] | None:
    """A transaction id (`{epoch ms}-{team_id}-{action}-{player_id}`, ffl/journal.py) as its parts.
    A D/ST's player id is negative, so its key reads `...-DROPPED--16020`. None when malformed."""
    parts = tx_id.split("-", 3)
    if len(parts) != 4 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    try:
        return parts[0], int(parts[1]), parts[2], int(parts[3])
    except ValueError:
        return None


TX_LEAVES = ("DROPPED", "TRADE_SENT")


def log_owners(picks: list[dict], rows: list[dict]) -> dict[int, int]:
    """player_id -> fantasy team id as the record has it: the draft, then the transaction log in
    time order. Within one timestamp every leaving row goes before any arriving one, so a trade's
    received row never loses to its sent row, whatever order the file holds them in."""
    owners = {p["player_id"]: p["team_id"] for p in picks}
    moves = sorted((int(ms), action not in TX_LEAVES, team, pid)
                   for r in rows if (t := parse_tx_id(r.get("id", ""))) for ms, team, action, pid in [t])
    for _, arrives, team, pid in moves:
        if arrives:
            owners[pid] = team
        elif owners.get(pid) == team:
            del owners[pid]
    return owners


def ownership_drift(espn: dict[int, int], expected: dict[int, int]) -> set[int]:
    """The players whose team on ESPN is not the one the record names (rostered by someone else,
    by nobody, or by somebody the record never saw take him)."""
    return {pid for pid in espn.keys() | expected.keys() if espn.get(pid) != expected.get(pid)}


def group_transactions(rows: list[dict], info: dict[int, dict], week1_start: date) -> list[dict]:
    """The transactions table's rows as moves, newest first. A waiver run stamps every claim with one
    timestamp across teams, so a move is one team's rows of one timestamp (its adds and drops,
    paired); a trade is every row of a timestamp that has a TRADE_* row, one side per team, where a
    side's `added` is what it received and `dropped` what it cut to make room (what it sent is the
    other side's `added`). `info` is player id -> pos and NFL team; a player it lacks gets None."""
    parsed = [(p, r) for r in rows if (p := parse_tx_id(r.get("id", "")))]
    trades = {ms for (ms, _, action, _), _ in parsed if action.startswith("TRADE")}
    groups: dict[str, list] = {}
    for p, r in parsed:
        ms, team_id = p[0], p[1]
        groups.setdefault(ms if ms in trades else f"{ms}-{team_id}", []).append((p, r))
    moves = []
    for key, members in groups.items():
        sides: dict[int, dict] = {}
        for (_, team_id, action, pid), r in members:
            side = sides.setdefault(team_id, {"team_id": team_id, "team": (r.get("team") or "").strip(),
                                              "added": [], "dropped": []})
            if action == "TRADE_SENT":
                continue  # the other side's TRADE_RECEIVED
            known = info.get(pid, {})
            player = {"player_id": pid, "name": r.get("player", ""), "pos": known.get("pos"), "team": known.get("team")}
            side["dropped" if action == "DROPPED" else "added"].append(player)
        at = members[0][1].get("date", "")
        if key in trades:
            kind = "trade"
        else:
            kind = next((TX_KIND[a] for (_, _, a, _), _ in members if a in TX_KIND), "drop")
        moves.append({"id": key, "ms": int(members[0][0][0]), "at": at, "kind": kind,
                      "week": max(0, week_of(datetime.fromisoformat(at).date(), week1_start)) if at else 0,
                      "sides": list(sides.values())})
    moves.sort(key=lambda m: -m["ms"])
    return moves


def player_transactions(moves: list[dict], player_id: int) -> list[dict]:
    """One player's moves (group_transactions' output, newest first), each read from the side he
    was on: `kind` is the move's for an add, "traded" for a trade's receiving side, "dropped" for a
    cut; `with` is the other half of his pair (who was cut for him, who he was cut for, what went
    the other way in a trade) and `from` the trade partner. ESPN keeps no link between a claim and
    its cut, so a move of several pairs line for line in listed order, as the Transactions page
    does (web/lib/transactions.ts `pairs`): the second claim reads the second cut, a cut past the
    last claim reads nobody."""
    def pair(mine: list[dict], theirs: list[dict]) -> list[dict]:
        i = next(i for i, p in enumerate(mine) if p["player_id"] == player_id)
        return theirs[i:i + 1]

    out = []
    for m in moves:
        for s in m["sides"]:
            added = any(p["player_id"] == player_id for p in s["added"])
            if not added and not any(p["player_id"] == player_id for p in s["dropped"]):
                continue
            partner = next((o for o in m["sides"] if o is not s), None) if m["kind"] == "trade" else None
            if partner is not None and added:
                kind, others = "traded", partner["added"]  # a trade is one deal: everything that went the other way
            elif not added:
                kind, others = "dropped", pair(s["dropped"], s["added"])
            else:
                kind, others = m["kind"], pair(s["added"], s["dropped"])
            out.append({"id": m["id"], "at": m["at"], "week": m["week"], "kind": kind,
                        **{k: s.get(k) for k in ("team_id", "team", "abbrev", "logo")},
                        "with": [p["name"] for p in others],
                        "from": {"team_id": partner["team_id"], "team": partner["team"]} if kind == "traded" else None})
    return out


def draft_entry(picks: list[dict], player_id: int, clubs: dict[int, dict]) -> dict | None:
    """His draft pick (espn_season.draft_picks) as a history entry, under the drafting team's
    current name and mark (`clubs`, team id -> club); a team gone keeps the logged name. None
    when he went undrafted."""
    p = next((p for p in picks if p["player_id"] == player_id), None)
    if p is None:
        return None
    club = clubs.get(p["team_id"]) or {"team": p.get("team", ""), "abbrev": "", "logo": ""}
    return {"id": f"draft-{p['overall']}", "at": p.get("at"), "week": 0, "kind": "draft",
            "team_id": p["team_id"], "team": club["team"], "abbrev": club.get("abbrev", ""), "logo": club.get("logo", ""),
            "with": [], "from": None, "round": p["round"], "pick": p["pick"], "overall": p["overall"]}


def matchup_final(m: dict, games: dict[str, dict] | None = None) -> bool:
    """A League scoreboard matchup's score is final: both sides have a lineup and every starter's
    game is over, by ESPN's game_played (a side's `pending_teams` are the clubs it does not yet
    call played) or, sooner, by the live feed's whistle (`games`, team -> status "post"). A game
    the feed has in progress is never over: ESPN's game_played flips three hours after kickoff,
    game or no game, and a Monday night game runs past that."""
    sides = (m["home"], m["away"])
    if not all(s.get("starter_ids") for s in sides):
        return False  # a future week's pairing: no lineups, nothing played
    games = games or {}
    if any(games.get(t, {}).get("status") == "in" for s in sides for t in s.get("starter_teams") or []):
        return False
    return all((games or {}).get(t, {}).get("status") == "post" for s in sides for t in s.get("pending_teams") or [])


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


def _fold(text: str) -> str:
    """Lower case, accents off, and the punctuation names carry (St. Brown, Ja'Marr, Amon-Ra) out,
    hyphens to spaces, so what is typed matches however ESPN spells it."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = t.replace("-", " ")
    return " ".join("".join(c for c in t if c.isalnum() or c == " ").split())


def name_matches(p: dict, q: str) -> bool:
    """The Waivers search: any part of his name, or his club's abbreviation in full (DET lists the
    Lions, DE does not). A blank query matches nobody; the caller treats it as no search."""
    q = _fold(q)
    if not q:
        return False
    return q in _fold(p.get("name") or "") or q.upper() == (p.get("team") or "").upper()


def rank_players(rows: list[dict]) -> list[dict]:
    """The Players table's order: one row per player, a rostered row over a free-agent one for the
    same man (ESPN can list a just-added player as free for a moment), then add best first with
    the rows that have none (my own players) last."""
    by_id: dict[int, dict] = {}
    for r in rows:
        if r["player_id"] not in by_id or r.get("owner"):
            by_id[r["player_id"]] = r
    return sorted(by_id.values(), key=lambda r: (r.get("add") is None, -(r.get("add") or 0)))


def lineup_moves(moves: list[dict], roster: list[dict], settings: dict) -> tuple[list[tuple[int, str, str]], list[str]]:
    """A requested set of slot moves as ESPN's items (player, from, to), or why not. Refused: anything that leaves an
    IR player with no designation on IR (ESPN blocks every other move until he is off), a player not on the roster,
    a locked player (his game has started), a move into IR for anyone not listed IR or Out, a slot this league does
    not have or that does not take him, and any starter slot or IR left holding more players than it has spots."""
    by_id = {p["player_id"]: p for p in roster}
    slots = {p["player_id"]: p.get("slot") for p in roster}
    off = {m["player_id"] for m in moves if m["to_slot"] != "IR"}
    stuck = [p for p in ir_illegal(roster) if p["player_id"] not in off]
    if stuck:
        return [], [f"{p['name']} is on IR without an injury designation — move him off IR first" for p in stuck]
    items: list[tuple[int, str, str]] = []
    errs: list[str] = []
    for m in moves:
        p, to = by_id.get(m["player_id"]), m["to_slot"]
        if p is None:
            errs.append(f"player {m['player_id']} is not on the roster")
        elif p.get("locked"):
            errs.append(f"{p['name']}: his game has started")
        elif to == "IR" and p.get("slot") != "IR" and p.get("injury") not in IR_ENTRY:
            errs.append(f"{p['name']}: only IR or Out can go on IR")
        elif to not in BENCH_SLOTS and (to not in settings["starters"] or p["pos"] not in _slot_accepts(to)):
            errs.append(f"{p['name']}: {slot_label(to)} does not take a {p['pos']}")
        elif to != p.get("slot"):
            items.append((p["player_id"], p["slot"], to))
            slots[p["player_id"]] = to
    if not errs:
        spots = {**settings["starters"], "IR": ir_size(settings)}
        filled = Counter(s for s in slots.values() if s != "BE")
        errs = [f"{slot_label(s)}: {n} players for {spots.get(s, 0)} spots"
                for s, n in filled.items() if n > spots.get(s, 0)]
    return items, errs


def reseat(items: list[tuple[int, str, str]], cards: list[dict], seats: dict[int, int]) -> dict[int, int]:
    """A move keeps the rows: a player moved into a starting slot takes the seat of the one who left it, in the
    order the page shows them (`espn_order`), first in for first out. Returns the new seats (player -> the player
    whose place in ESPN's order he stands in; nobody listed stands in his own). lineup.applyMoves mirrors it."""
    order = {c["player_id"]: c.get("espn_order", 0) for c in cards}
    out = {}
    for slot in {t for _, _, t in items if t not in BENCH_SLOTS}:
        leaving = sorted((p for p, f, _ in items if f == slot), key=lambda p: order.get(p, 0))
        entering = sorted((p for p, _, t in items if t == slot), key=lambda p: order.get(p, 0))
        out.update({e: seats.get(l, l) for e, l in zip(entering, leaving)})
    return out


def lineup_entry(items: list[tuple[int, str, str]], cards: list[dict], week: int) -> dict:
    """The decisions row for moves made from the Roster page: what moved, and the call and gap each player carried
    at that moment. A move that follows the call is recorded as such; any other is left for a rationale."""
    by = {c["player_id"]: c for c in cards}
    where = lambda s: "the bench" if s == "BE" else slot_label(s)  # noqa: E731
    followed = decisions.followed_lineup(items, cards)
    return {"at": decisions.now_iso(), "week": week, "kind": "lineup", "source": "app",
            "title": f"Week {week} lineup: " + ", ".join(f"{by[p]['name']} to {where(t)}" for p, _, t in items),
            "decision": "; ".join(f"{by[p]['name']} ({by[p]['pos']}) {slot_label(f)} → {slot_label(t)}" for p, f, t in items),
            "facts": {"moves": [{"player_id": p, "name": by[p]["name"], "pos": by[p]["pos"], "from": f, "to": t,
                                 "call": by[p]["call"], "gap": by[p].get("gap")} for p, f, t in items]},
            "followed_call": followed, "rationale": ["followed_call"] if followed else None,
            "player_ids": [p for p, _, _ in items]}


def sit_entry(card: dict, sat: bool, week: int) -> dict:
    """The decisions row for a sit toggled on the Roster page: who, and the call he carried then."""
    kind = "sit" if sat else "unsit"
    followed = decisions.followed_sit(card, sat)
    what = "sat for the week, left out of the lineup call" if sat else "back in the lineup call"
    return {"at": decisions.now_iso(), "week": week, "kind": kind, "source": "app",
            "title": f"Week {week} {kind}: {card['name']}", "decision": f"{card['name']} ({card['pos']}) {what}",
            "facts": {"call": card.get("model_call") or card["call"], "reason": card["reason"]},
            "followed_call": followed, "rationale": ["followed_call"] if followed else None,
            "player_ids": [card["player_id"]]}
