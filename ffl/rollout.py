"""Monte Carlo rollout: a second opinion beside the candidate table.

For each candidate at the pick I am choosing for, play the rest of the draft out many times —
opponents draft to 2QB ADP with the spread the feed gives each player, constrained by what
their rosters can still use; my own later picks are greedy on lineup value — and score the
candidate by the season points of my best legal lineup at the end, bye weeks included.

This replaces nothing. `recommend()`'s `score` stays the default ordering; the rollout is
printed beside it (`FFL_ROLLOUT=1`) so the two can be read against each other. Where they
disagree, the hard rules in the league's strategy (`uv run python -m ffl.strategy show`) and the human decide. It writes nothing, and
is pure over its inputs: the callers pass the league's `caps` and `qb_pace` (`strategy.caps`, `strategy.qb_pace`).

Why a rollout at all: the table estimates survival one player at a time and prices the cost
of waiting per position. Those are heuristics for a joint question — "given who is likely to
be gone when I pick next, and next after that, what does taking this man now do to the roster
I end up with?" — and the rollout answers that question directly.
"""
from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import board as board_mod
from .draft_watch import DEFAULT_ADP_SD, POSITIONS, SEASON_WEEKS, _is_flex

# The roster caps the opponents (and my own later picks) respect are the league's (`strategy.caps`,
# passed in by the caller and handed to every Team): in the home league QB 3 and TE 2 are the
# strategy's blacklists, K/D/ST one each, and RB/WR 7 a sanity cap so a simulated team cannot
# draft nine running backs when the ADP noise happens to line up that way. Opponents get my caps
# too: theirs are unknown.
# Nobody in a human room takes a kicker or defense before their last few picks. Mirrors the
# recommender's own gate (off the board until the remaining picks only cover empty slots),
# with a little slack because humans are not that disciplined.
KDST_PICKS_LEFT = 3
# Players the ADP feed does not cover go undrafted or late; give them an ADP past the feed's
# tail, ordered by projection so the best of them go first.
UNRANKED_ADP_START = 230.0
# Points a bench player is worth in my own greedy policy, as a fraction of his projection:
# the first backup at a position covers a bye or an injury, the second covers less. Only my
# in-simulation picks use this; the candidate's score is always the final lineup.
BENCH_VALUE = {0: 0.12, 1: 0.05}
BENCH_VALUE_DEEP = 0.01


@dataclass
class Pool:
    """The available players as flat arrays, indexed by position in the pool."""
    ids: np.ndarray
    names: list[str]
    pos: list[str]
    proj: np.ndarray
    bye: np.ndarray            # NaN when unknown
    adp: np.ndarray
    adp_sd: np.ndarray
    repl: dict[str, float] = field(default_factory=dict)   # the board's replacement level per position
    by_pos: dict[str, list[int]] = field(default_factory=dict)

    @classmethod
    def from_board(cls, bd: pd.DataFrame, qb_pace: float | None = None) -> "Pool":
        avail = bd[~bd.drafted].copy()
        # unranked players: after the feed, in projection order
        unranked = avail.adp.isna()
        if unranked.any():
            order = avail[unranked].proj.rank(ascending=False, method="first")
            avail.loc[unranked, "adp"] = UNRANKED_ADP_START + order
        # QB pace: how many picks earlier than the FFC feed this room takes its quarterbacks.
        # The simulated room matches FFC's 2QB logs within ~5 picks at the 20th, 24th and 28th
        # QB with this at 0 (measured 2026-09-07); a league where every manager hurries a QB3
        # is faster, and nothing measures by how much until the real draft. Applied to the
        # opponents' draw order and to my own plan's survival expectations alike.
        pace = qb_pace or 0.0  # the callers pass the league's (strategy.qb_pace)
        if pace:
            qb = avail.pos == "QB"
            avail.loc[qb, "adp"] = (avail.loc[qb, "adp"] - pace).clip(lower=1.0)
        sd = avail["adp_stdev"] if "adp_stdev" in avail.columns else pd.Series(np.nan, index=avail.index)
        sd = pd.to_numeric(sd, errors="coerce").to_numpy(float)
        sd = np.where(np.isnan(sd) | (sd <= 0), DEFAULT_ADP_SD, sd)
        bye = pd.to_numeric(avail["bye"], errors="coerce").to_numpy(float) if "bye" in avail.columns \
            else np.full(len(avail), np.nan)
        pool = cls(ids=avail.player_id.to_numpy(int), names=list(avail.name), pos=list(avail.pos),
                   proj=avail.proj.to_numpy(float), bye=bye, adp=avail.adp.to_numpy(float), adp_sd=sd)
        # by projection, best first: the board frame arrives in blend order, which is not that
        for i in np.argsort(-pool.proj, kind="stable"):
            pool.by_pos.setdefault(pool.pos[i], []).append(int(i))
        if "repl" in avail.columns:
            pool.repl = {p: float(v) for p, v in avail.groupby("pos").repl.first().items()}
        return pool

    def __len__(self):
        return len(self.ids)


@dataclass
class Team:
    counts: dict[str, int]
    picks_left: int
    caps: dict[str, int] | None = None     # per position, a missing one uncapped; None: none capped

    def __post_init__(self):
        if self.caps is None:
            self.caps = {}


def starter_slots(settings: dict) -> tuple[dict[str, int], int, set[str]]:
    """Fixed starting slots per position, the flex count, and which positions the flex takes."""
    fixed, flex_n, flex_pos = {}, 0, set()
    for slot, n in settings["starters"].items():
        if _is_flex(slot):
            flex_n += n
            flex_pos |= {"QB", "RB", "WR", "TE"} if slot == "OP" else set(slot.split("/"))
        else:
            fixed[slot] = n
    return fixed, flex_n, flex_pos


def lineup_points(proj: list[float], pos: list[str], settings: dict) -> float:
    """Season points of the best legal lineup from these players, byes ignored.

    Fixed slots take the best at their position; the flex takes the best of what is left at
    the positions it accepts. That greedy fill is optimal because a fixed slot can only hold
    its own position, so nothing is gained by holding a better player back for the flex.
    """
    fixed, flex_n, flex_pos = starter_slots(settings)
    by_pos: dict[str, list[float]] = {}
    for v, p in zip(proj, pos):
        by_pos.setdefault(p, []).append(v)
    total = 0.0
    leftovers: list[float] = []
    for p, vals in by_pos.items():
        vals.sort(reverse=True)
        n = fixed.get(p, 0)
        total += sum(vals[:n])
        if p in flex_pos:
            leftovers.extend(vals[n:])
    if flex_n and leftovers:
        leftovers.sort(reverse=True)
        total += sum(leftovers[:flex_n])
    return total


def season_points(proj: list[float], pos: list[str], bye: list[float], settings: dict) -> float:
    """Season points of the best legal lineup, re-picked every week with bye players out.

    Each player is worth proj / weeks left a week. This is the number a candidate is scored
    on: it is what the roster actually fields, and it is where two starters sharing a bye
    cost something and a backup at a thin position earns his keep.
    """
    first = board_mod.start_week(settings)
    n_weeks = board_mod.weeks_left(settings)
    weeks = sorted({int(b) for b in bye if b is not None and not (isinstance(b, float) and math.isnan(b))
                    and int(b) >= first})  # a bye already played is no week to lose
    full = lineup_points(proj, pos, settings) / n_weeks
    total = full * (n_weeks - len(weeks))
    for w in weeks:
        keep_p = [v for v, b in zip(proj, bye) if not (b is not None and not math.isnan(b) and int(b) == w)]
        keep_pos = [q for q, b in zip(pos, bye) if not (b is not None and not math.isnan(b) and int(b) == w)]
        total += lineup_points(keep_p, keep_pos, settings) / n_weeks
    return total


def open_starters(counts: dict[str, int], settings: dict) -> dict[str, int]:
    fixed, _, _ = starter_slots(settings)
    return {p: max(n - counts.get(p, 0), 0) for p, n in fixed.items()}


def flex_open(counts: dict[str, int], settings: dict) -> int:
    fixed, flex_n, flex_pos = starter_slots(settings)
    spill = sum(max(counts.get(p, 0) - fixed.get(p, 0), 0) for p in flex_pos)
    return max(flex_n - spill, 0)


def allowed_positions(team: Team, settings: dict) -> set[str]:
    """Which positions this team may draft at its next pick, given its roster and picks left."""
    left = open_starters(team.counts, settings)
    unfilled = sum(left.values())
    if team.picks_left <= unfilled:
        # Only the empty starting slots fit in the picks that remain: fill them and nothing else.
        return {p for p, n in left.items() if n > 0}
    ok = set()
    for p in POSITIONS:
        if team.counts.get(p, 0) >= team.caps.get(p, 99):
            continue
        if p in ("K", "D/ST") and team.picks_left > KDST_PICKS_LEFT + left.get(p, 0) - 1:
            continue
        ok.add(p)
    return ok


def opponent_pick(order: np.ndarray, taken: np.ndarray, pool: Pool, team: Team, settings: dict) -> int:
    """The first player, in this simulation's perturbed ADP order, this team can use."""
    ok = allowed_positions(team, settings)
    for i in order:
        if not taken[i] and pool.pos[i] in ok:
            return int(i)
    return _anyone_left(taken, pool, team)


def _norm_sf(z: np.ndarray) -> np.ndarray:
    """1 - Phi(z), vectorised (Abramowitz & Stegun 7.1.26, error under 2e-7)."""
    x = np.abs(z) / math.sqrt(2)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-x * x)
    erf = np.where(z < 0, -erf, erf)
    return 0.5 * (1.0 - erf)


PLAN_DEPTH_PLAYERS = 60  # per position, by projection: deeper than this the replacement level decides


def expected_at_picks(pool: Pool, taken: np.ndarray, pos: str, picks: list[int], depth: int) -> np.ndarray:
    """For each of my picks, the expected projection of the best (and second best, ...)
    player at `pos` still there when it comes up.

    picks[0] is the pick being decided, where everyone available is certain. For a later pick
    q, a player available now survives with probability P(X >= q | X >= now) under the normal
    the survival column uses (his ADP mean and spread); the expected best is the sum over
    players, best first, of proj x P(he survives and everyone better does not), and the
    expected second best counts the ways exactly one better player survives. The league's
    replacement level stands in for the case that nobody does.

    A hard ADP cut-off was tried first and treated a 50% survivor as a certainty; on the QB
    plateau that meant deferring the second QB pick by pick until only the cliff was left.

    Shape (len(picks), depth).
    """
    idx = [i for i in pool.by_pos.get(pos, []) if not taken[i]][:PLAN_DEPTH_PLAYERS]  # proj desc
    repl = pool.repl.get(pos, 0.0)
    out = np.full((len(picks), depth), repl)
    if not idx or not picks:
        return out
    proj = pool.proj[idx][:, None]
    adp = pool.adp[idx][:, None]
    sd = pool.adp_sd[idx][:, None]
    now = picks[0]
    q = np.asarray(picks, dtype=float)[None, :]
    p_now = np.maximum(_norm_sf((now - 0.5 - adp) / sd), 1e-9)
    s = np.clip(_norm_sf((q - 0.5 - adp) / sd) / p_now, 0.0, 1.0)
    s[:, 0] = 1.0                                    # the pick on the clock: he is here
    s = np.clip(s, 1e-9, 1 - 1e-9)
    none_better = np.vstack([np.ones((1, s.shape[1])), np.cumprod(1 - s, axis=0)[:-1]])  # (n, P)
    out[:, 0] = (proj * s * none_better).sum(axis=0) + repl * np.prod(1 - s, axis=0)
    if depth > 1:
        ratio = s / (1 - s)
        one_better = none_better * np.vstack([np.zeros((1, s.shape[1])), np.cumsum(ratio, axis=0)[:-1]])
        p_le1_all = np.prod(1 - s, axis=0) * (1 + ratio.sum(axis=0))
        out[:, 1] = (proj * s * one_better).sum(axis=0) + repl * p_le1_all
        for k in range(2, depth):
            out[:, k] = np.minimum(out[:, k - 1], repl)   # deeper than two: settle for replacement
    return out


def assign_max(value: np.ndarray) -> list[int]:
    """Assignment of rows to distinct columns maximising the total (Hungarian, small sizes).

    Rectangular: with more columns than rows some columns stay free; with more rows than
    columns the surplus rows get -1. Cost is O(rows^2 x cols), pure Python, fine at 11 x 17.
    """
    n_rows, n_cols = value.shape
    if n_rows > n_cols:
        col = assign_max(value.T)  # assign columns to rows instead, then invert
        out = [-1] * n_rows
        for c, r in enumerate(col):
            if r >= 0:
                out[r] = c
        return out
    n, m = n_rows, n_cols
    cost = (-value).tolist()  # minimise the negative
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (m + 1)
    p = [0] * (m + 1)       # column -> row
    way = [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (m + 1)
        used = [False] * (m + 1)
        row = None
        while True:
            used[j0] = True
            i0 = p[j0]
            row = cost[i0 - 1]
            ui = u[i0]
            delta = INF
            j1 = 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = row[j - 1] - ui - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    col_of_row = [-1] * n_rows
    for j in range(1, m + 1):
        if p[j]:
            col_of_row[p[j] - 1] = j - 1
    return col_of_row


def plan_position(pool: Pool, taken: np.ndarray, team: Team, settings: dict, my_picks: list[int]) -> str | None:
    """Which position to take at my_picks[0], from a plan for every pick I have left.

    Each open starting slot must be filled by one of my remaining picks, and what a slot is
    worth depends on when it is filled: the best available at a position only gets worse
    with every pick, and at different rates — the QB plateau is flat for two rounds and then
    drops off a cliff, while WR falls steadily. So assign slots to picks to maximise the total
    expected projection (Hungarian on a slots x picks matrix), and take now whatever the plan
    fills now. A bench slot's value is a slice of the best projection available at that pick.

    This is what a cost-of-waiting rule cannot do one step at a time: it saw a cheap wait on
    QB at every single pick and finished with Willis and Daniel Jones at quarterback.

    Returns None when nothing usable is left.
    """
    ok = allowed_positions(team, settings)
    if not ok:
        return None
    fixed, flex_n, flex_pos = starter_slots(settings)
    left = open_starters(team.counts, settings)
    n_flex = flex_open(team.counts, settings)
    slots: list[tuple[str, int]] = []           # (position or "FLEX", rank within position)
    for p, n in left.items():
        slots += [(p, k) for k in range(n)]
    slots += [("FLEX", k) for k in range(n_flex)]
    if not slots:
        return "BENCH"                          # every starter is set: nothing left to plan
    depth = {p: left.get(p, 0) + (n_flex if p in flex_pos else 0) for p in POSITIONS}
    expect = {p: expected_at_picks(pool, taken, p, my_picks, max(depth[p], 1)) for p in POSITIONS if depth[p] > 0}
    n_bench = max(team.picks_left - len(slots), 0)
    rows = []
    for p, k in slots:
        if p == "FLEX":
            # the flex takes the best of its positions left after their own slots
            cand = [expect[q][:, left.get(q, 0) + k] for q in flex_pos if q in expect]
            rows.append(np.max(cand, axis=0) if cand else np.zeros(len(my_picks)))
        else:
            rows.append(expect[p][:, k])
    if n_bench:
        # a bench pick is worth a slice of the best skill player expected at that pick
        best_any = np.max([e[:, 0] for p, e in expect.items() if p in ("QB", "RB", "WR", "TE")] or [np.zeros(len(my_picks))], axis=0)
        rows += [best_any * BENCH_VALUE.get(0, 0.1)] * n_bench
    value = np.array(rows)
    col = assign_max(value)
    now = [r for r, c in enumerate(col) if c == 0]
    if not now:
        return None
    r = now[0]
    if r < len(slots):
        p, k = slots[r]
        if p == "FLEX":
            cand = {q: expect[q][0, left.get(q, 0) + k] for q in flex_pos if q in expect and q in ok}
            return max(cand, key=cand.get) if cand else None
        return p if p in ok else None
    return "BENCH"


def my_pick(taken: np.ndarray, pool: Pool, mine: list[int], team: Team, settings: dict,
            have_proj: list[float] | None = None, have_pos: list[str] | None = None,
            my_picks: list[int] | None = None, per_pos: int = 4) -> int:
    """My pick inside a simulation: the position the plan fills now, its best available player.

    `my_picks` are the overall numbers of this pick and every one I have left; the plan
    (`plan_position`) decides the position. Without it, or for a bench pick, fall back to the
    biggest lineup gain, with bench value as the tie-break: a slice of projection, thinner
    with every backup already held.
    """
    ok = allowed_positions(team, settings)
    cur_proj = (have_proj or []) + [pool.proj[i] for i in mine]
    cur_pos = (have_pos or []) + [pool.pos[i] for i in mine]
    base = lineup_points(cur_proj, cur_pos, settings)
    fixed, _, flex_pos = starter_slots(settings)
    planned = plan_position(pool, taken, team, settings, my_picks) if my_picks else None
    if planned not in (None, "BENCH"):
        i = next((j for j in pool.by_pos.get(planned, []) if not taken[j]), -1)
        if i >= 0:
            return i
    best, best_val = -1, -1e9
    for p in ok:
        cands = [i for i in pool.by_pos.get(p, []) if not taken[i]][:per_pos]
        repl = pool.repl.get(p, 0.0)
        for i in cands:
            gain = lineup_points(cur_proj + [pool.proj[i]], cur_pos + [p], settings) - base
            if gain > 0 and planned != "BENCH":
                val = max(gain - repl, 0.0) + min(gain, repl) * 0.05
            else:
                backups = max(team.counts.get(p, 0) - fixed.get(p, 0)
                              - (flex_open(team.counts, settings) if p in flex_pos else 0), 0)
                val = pool.proj[i] * BENCH_VALUE.get(backups, BENCH_VALUE_DEEP)
            if val > best_val:
                best, best_val = i, val
    if best < 0:
        best = _anyone_left(taken, pool, team)
    return best


def _anyone_left(taken: np.ndarray, pool: Pool, team: Team) -> int:
    """Nothing usable is left: the best projection under the caps, else anyone at all."""
    under_cap = [i for i in np.argsort(-pool.proj) if not taken[i]
                 and team.counts.get(pool.pos[i], 0) < team.caps.get(pool.pos[i], 99)]
    if under_cap:
        return int(under_cap[0])
    left = np.flatnonzero(~taken)
    return int(left[0]) if len(left) else -1


def candidate_indices(pool: Pool, rec: dict, my_counts: dict[str, int], settings: dict, n_top: int = 8,
                      caps: dict[str, int] | None = None) -> list[int]:
    """The score top-N plus the best available at every position with an open starting slot
    (caps: none capped when None)."""
    caps = caps or {}
    id_to_idx = {int(pid): i for i, pid in enumerate(pool.ids)}
    out: list[int] = []
    for r in rec["top"][:n_top]:
        i = id_to_idx.get(int(r["player_id"]))
        if i is not None and r.get("score", 0) > -900 and i not in out:
            out.append(i)
    left = open_starters(my_counts, settings)
    if flex_open(my_counts, settings):
        for p in starter_slots(settings)[2]:
            left[p] = left.get(p, 0) + 1
    for p, n in left.items():
        # the flex adds every flex position, including one already at its roster cap (a
        # third TE via the flex is how a rollout-drafted slot 4 ended with three tight ends)
        if n <= 0 or p in ("K", "D/ST") or my_counts.get(p, 0) >= caps.get(p, 99):
            continue
        for i in pool.by_pos.get(p, []):
            if i not in out:
                out.append(i)
            break
    return out


def rollout(bd: pd.DataFrame, settings: dict, full_order: list[int], me: int, decide: int,
            rosters: dict[int, dict[str, int]], my_roster: pd.DataFrame, rec: dict, n_sims: int = 300,
            time_budget_s: float | None = None, seed: int | None = None,
            candidates: list[int] | None = None, caps: dict[str, int] | None = None,
            qb_pace: float | None = None) -> dict:
    """Score each candidate for my pick at overall number `decide` by the roster it leads to.

    rosters: position counts per team id for everyone drafted so far (mine included).
    my_roster: the board rows of the players I already hold (they are off the pool, so their
        projections and byes ride in here for the final evaluation).
    caps: the roster caps every simulated team respects (the league's, `strategy.caps`; None: none capped).
    qb_pace: picks earlier than ADP the room takes its QBs (the league's, `strategy.qb_pace`; None: 0).
    Returns {"rows": [...sorted by roll desc...], "n_sims": achieved, "elapsed": seconds}.
    Each row: player_id, name, pos, roll (mean season lineup points), roll_se, next_pos and
    next_proj (what my following pick most often is, and its mean projection) — the reason
    the candidate scores as he does, in one line.
    """
    pool = Pool.from_board(bd, qb_pace)
    rng = np.random.default_rng(seed)
    caps = caps or {}
    my_counts = dict(rosters.get(me, {}))
    if candidates is None:
        cand = candidate_indices(pool, rec, my_counts, settings, caps=caps)
    else:
        id_to_idx = {int(pid): i for i, pid in enumerate(pool.ids)}
        cand = [id_to_idx[c] for c in candidates if c in id_to_idx]
    if not cand:
        return {"rows": [], "n_sims": 0, "elapsed": 0.0}
    remaining = full_order[decide - 1:]  # the pick I am choosing for, then everyone after
    assert remaining and remaining[0] == me, "decide must be one of my picks"
    picks_left = Counter(remaining)
    have_proj, have_pos, have_bye = _existing_roster(my_roster)
    # for each of my picks in `remaining` (by index), the overall numbers of it and every one after
    my_idx = [k for k, t in enumerate(remaining) if t == me]
    my_picks_at = {k: [decide + j for j in my_idx if j >= k] for k in my_idx}

    results = {i: [] for i in cand}
    nexts = {i: [] for i in cand}
    t0 = time.perf_counter()
    sims_done = 0
    while sims_done < n_sims:
        if time_budget_s is not None and sims_done >= 20 and time.perf_counter() - t0 > time_budget_s:
            break
        # one perturbed ADP order per simulation, shared by every candidate so their
        # comparison is paired (same opponents, same noise, different first move)
        noise = rng.normal(0.0, 1.0, len(pool)) * pool.adp_sd
        order = np.argsort(pool.adp + noise, kind="stable")
        for c in cand:
            taken = np.zeros(len(pool), dtype=bool)
            teams = {t: Team(dict(rosters.get(t, {})), n, caps) for t, n in picks_left.items()}
            mine: list[int] = []
            for k, t in enumerate(remaining):
                team = teams[t]
                if t == me:
                    i = c if k == 0 else my_pick(taken, pool, mine, team, settings, have_proj, have_pos,
                                                 my_picks_at[k])
                    if i < 0:
                        break
                    mine.append(i)
                else:
                    i = opponent_pick(order, taken, pool, team, settings)
                    if i < 0:
                        break
                taken[i] = True
                team.counts[pool.pos[i]] = team.counts.get(pool.pos[i], 0) + 1
                team.picks_left -= 1
            proj = have_proj + [pool.proj[i] for i in mine]
            pos = have_pos + [pool.pos[i] for i in mine]
            bye = have_bye + [pool.bye[i] for i in mine]
            results[c].append(season_points(proj, pos, bye, settings))
            if len(mine) > 1:
                nexts[c].append((pool.pos[mine[1]], pool.proj[mine[1]]))
        sims_done += 1
    rows = []
    leader = max(cand, key=lambda c: float(np.mean(results[c])))
    lead_vals = np.array(results[leader])
    for c in cand:
        vals = np.array(results[c])
        diff = vals - lead_vals
        nx = nexts[c]
        next_pos = Counter(p for p, _ in nx).most_common(1)[0][0] if nx else ""
        next_proj = float(np.mean([v for p, v in nx if p == next_pos])) if nx else float("nan")
        rows.append({"player_id": int(pool.ids[c]), "name": pool.names[c], "pos": pool.pos[c],
                     "proj": float(pool.proj[c]),
                     "roll": round(float(vals.mean()), 1),
                     "roll_se": round(float(vals.std(ddof=1) / math.sqrt(len(vals))), 1) if len(vals) > 1 else float("nan"),
                     # The simulations are paired (same opponents, same noise, different first
                     # move), so the error on the gap to the leader is much smaller than the
                     # error on either mean. The gap is what gets read.
                     "gap_se": round(float(diff.std(ddof=1) / math.sqrt(len(diff))), 1) if len(vals) > 1 else float("nan"),
                     "next_pos": next_pos, "next_proj": round(next_proj, 0) if nx else float("nan")})
    rows.sort(key=lambda r: -r["roll"])
    return {"rows": rows, "n_sims": sims_done, "elapsed": round(time.perf_counter() - t0, 1)}


def _existing_roster(my: pd.DataFrame | None) -> tuple[list[float], list[str], list[float]]:
    """Projection, position and bye of the players I already hold."""
    if my is None or not len(my):
        return [], [], []
    proj = [float(v) for v in my.proj]
    pos = list(my.pos)
    bye = [float(b) if pd.notna(b) else float("nan") for b in my.bye] if "bye" in my.columns \
        else [float("nan")] * len(my)
    return proj, pos, bye


def enabled() -> bool:
    """The rollout is off unless asked for: nothing rehearsed changes without FFL_ROLLOUT=1."""
    import os
    return os.environ.get("FFL_ROLLOUT", "") == "1"


def from_state(bd: pd.DataFrame, settings: dict, full_order: list[int], me: int, tm: dict,
               by_team: dict[int, dict[str, int]], my_roster: pd.DataFrame, rec: dict,
               n_sims: int = 300, time_budget_s: float | None = 12.0, seed: int | None = None,
               caps: dict[str, int] | None = None, qb_pace: float | None = None) -> dict:
    """The rollout for the pick `turn_math` says I am choosing for. Empty when I have none left.

    FFL_ROLLOUT_SIMS overrides the simulation count (fewer on a slow machine; tests use 20).
    """
    import os
    if tm.get("decide") is None:
        return {"rows": [], "n_sims": 0, "elapsed": 0.0}
    n_sims = int(os.environ.get("FFL_ROLLOUT_SIMS") or n_sims)
    return rollout(bd, settings, full_order, me, tm["decide"], by_team, my_roster, rec,
                   n_sims=n_sims, time_budget_s=time_budget_s, seed=seed, caps=caps, qb_pace=qb_pace)


def rollout_lines(res: dict, rec: dict, n: int = 3) -> list[str]:
    """The lines printed under the candidate table when the rollout is on."""
    rows = res["rows"]
    if not rows:
        return ["rollout    (no candidates)"]
    top = rows[0]["roll"]
    parts = []
    for r in rows[:n]:
        nxt = f", then {r['next_pos']} ~{r['next_proj']:.0f}" if r["next_pos"] else ""
        parts.append(f"{r['name']} ({r['pos']}, {r['roll'] - top:+.1f}{nxt})")
    gap_se = max((r["gap_se"] for r in rows[1:n] if not math.isnan(r["gap_se"])), default=float("nan"))
    lines = [f"rollout    {'  '.join(parts)}   [n={res['n_sims']}, gap se~{gap_se}, {res['elapsed']}s]"]
    score_top5 = {int(r["player_id"]) for r in rec["top"][:5]}
    if rows[0]["player_id"] not in score_top5:
        # the gap that matters is to the best of what score would have you take; a lead of a
        # point over a bench body late in the draft is noise, not a disagreement
        best_scored = next((r for r in rows[1:] if r["player_id"] in score_top5), None)
        gap = top - best_scored["roll"] if best_scored else top - (rows[1]["roll"] if len(rows) > 1 else top)
        se = best_scored["gap_se"] if best_scored and not math.isnan(best_scored["gap_se"]) else 0.0
        if gap >= max(DISAGREE_MIN_GAP, 2 * se):
            lines.append(f"ROLLOUT DISAGREES  {rows[0]['name']} ({rows[0]['pos']}) leads the rollout by "
                         f"{gap:.1f} over {best_scored['name'] if best_scored else 'the field'} "
                         f"and is outside the score top five")
    return lines


DISAGREE_MIN_GAP = 3.0  # season points: below this the two rankings are saying the same thing


def verdict_line(res: dict, chosen_id: int) -> str:
    """One line on how the rollout sees a pick made by something else (score, or a human)."""
    rows = res["rows"]
    if not rows:
        return "rollout: (no candidates)"
    lead = rows[0]
    mine = next((r for r in rows if r["player_id"] == chosen_id), None)
    tail = f"  [n={res['n_sims']}]"
    if mine is None:
        return f"rollout: {lead['name']} {lead['pos']} leads; the pick was not a candidate{tail}"
    if mine["player_id"] == lead["player_id"]:
        second = rows[1] if len(rows) > 1 else None
        alt = f"; next {second['name']} {second['pos']} {second['roll'] - lead['roll']:+.1f}" if second else ""
        return f"rollout: agrees{alt}{tail}"
    return (f"rollout: {lead['name']} {lead['pos']} leads; {mine['name']} "
            f"{mine['roll'] - lead['roll']:+.1f} ±{mine['gap_se']}{tail}")


def roll_by_id(res: dict) -> dict[int, float]:
    top = res["rows"][0]["roll"] if res["rows"] else 0.0
    return {r["player_id"]: round(r["roll"] - top, 1) for r in res["rows"]}
