"""Live draft watcher: reads picks off the draft room socket, tracks every roster, and builds the
candidate table for my next pick.

    uv run python -m ffl.draft_watch            # live: read picks off the draft room socket
    uv run python -m ffl.draft_watch once       # single REST snapshot (pre-draft sanity check)
    uv run python -m ffl.draft_watch rest       # REST polling loop (post-draft recap only)

Outputs, all under the watched league (FFL_LEAGUE, else the home league): the DB's draft_picks
(the log), draft_states (full state, which ffl.autodraft reads) and draft_feeds (the raw socket
feed, which ffl.advise / ffl.avail read), and stdout. A practice run (FFL_DATA_DIR) writes the same
tables in its own data dir's ffl.db.
ESPN's REST mDraftDetail view is useless *during* a live draft — every slot stays at
playerId -1 until the draft finishes — so live mode reads the draft room's websocket
over CDP instead (see ffl/draft_socket.py). REST stays for pre-draft checks; whether it backfills after a draft is unverified.

Who decides: this module computes the facts nobody can work out in 90 seconds — who is left, what
I hold, the pick math for the drawn slot, value over the live pool, the cost of waiting until my
next turn, who survives that long, what the teams in between still need, bye clashes, injury
status. It emits a candidate table with all of that as columns. The call is made by a person (or
Claude) reading that table against the league's strategy (`uv run python -m ffl.strategy show`); the `score` order is a default, not a verdict.
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd

from . import board as board_mod
from . import config, db, leagues, strategy
from .config import raw_league_get

POLL_S = 5
NEED_WEIGHT = {"starter_open": 1.35, "flex_open": 1.1, "unstartable": 0.15}
POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]
# ADP spread to assume when the feed has none for a player (FFC's typical mid-round stdev)
DEFAULT_ADP_SD = 8.0
# Positions nobody takes as a spare early: a team picking before my next turn only takes one of
# these if it still has the slot open, so "likely gone" there is capped by that demand.
DEMAND_CAPPED = {"QB", "TE"}
INJURY_SHORT = {"ACTIVE": "", "QUESTIONABLE": "Q", "DOUBTFUL": "D", "OUT": "OUT", "DAY_TO_DAY": "DTD",
                "INJURY_RESERVE": "IR", "SUSPENSION": "SUSP"}


def my_team_id(settings: dict, ref=None) -> int:
    """Which team is mine, in *this* league: `leagues.my_team_id` (the SWID wins over the registry)."""
    return leagues.my_team_id(settings, ref)


def pick_order(settings: dict) -> list[int]:
    """The first-round order to start from.

    `FFL_PICK_ORDER=3,7,1,...` (team ids) wins, for the moment the room reveals the draw. Otherwise
    settings.json — which, when the league randomizes the order at draft start, is only a
    placeholder (team ids 1..10 in order) until ESPN fills it in. `learn_order` corrects it from
    the picks themselves as the draft goes.
    """
    env = os.environ.get("FFL_PICK_ORDER")
    ids = [t["id"] for t in settings["teams"]]
    if env:
        order = [int(x) for x in env.replace(" ", "").split(",") if x]
        if sorted(order) != sorted(ids):
            raise SystemExit(f"FFL_PICK_ORDER {order} does not match the league's team ids {sorted(ids)}")
        return order
    return list(settings["draft"].get("pick_order") or ids)


def order_is_placeholder(settings: dict) -> bool:
    """True when the order is randomized at draft start and settings still show ids 1..n."""
    order = settings["draft"].get("pick_order") or []
    ids = sorted(t["id"] for t in settings["teams"])
    return settings["draft"].get("order_type") == "DRAFT_START" and list(order) == ids \
        and not os.environ.get("FFL_PICK_ORDER")


def snake_order(pick_order: list[int], rounds: int) -> list[int]:
    out = []
    for r in range(rounds):
        out += pick_order if r % 2 == 0 else pick_order[::-1]
    return out


def learn_order(base: list[int], picks: list[dict], on_clock: int | None = None,
                current: int | None = None) -> list[int]:
    """The first-round order as the room reveals it, pick by pick.

    Every SELECTED frame names its team, so the first n picks *are* the order; the SELECTING frame
    names the team on the clock one pick ahead of that. Teams not yet seen keep their relative order
    from `base`. Until my own slot is revealed the pick math is a guess, which is why the runbook
    says to set the order (FFL_PICK_ORDER or a settings re-run) as soon as the room shows it.
    """
    n = len(base)
    seen: dict[int, int] = {}
    placed: set[int] = set()
    for p in picks[:n]:
        slot = int(p.get("overallPickNumber", p.get("overall", 0)) or 0) - 1
        team = p.get("teamId")
        if 0 <= slot < n and team in base and team not in placed:
            seen[slot] = team
            placed.add(team)
    if on_clock in base and on_clock not in placed and current is not None and 0 <= current - 1 < n:
        seen[current - 1] = on_clock
        placed.add(on_clock)
    if not seen:
        return list(base)
    rest = [t for t in base if t not in placed]
    return [seen[i] if i in seen else rest.pop(0) for i in range(n)]


def turn_math(full_order: list[int], me: int, current: int) -> dict:
    """Everything about my turns from the pick on the clock (`current`, 1-based) onward.

    decide: overall number of the pick I am choosing for (this one if I am on the clock).
    following: my pick after that. horizon: other teams' picks between the two — the span a
    player has to survive if I pass on him at `decide`. window: the teams making those picks.
    """
    idx = current - 1
    my_next = next((i for i in range(idx, len(full_order)) if full_order[i] == me), None)
    my_after = next((i for i in range(my_next + 1, len(full_order)) if full_order[i] == me), None) \
        if my_next is not None else None
    picks_until_me = (my_next - idx) if my_next is not None else -1
    horizon = (my_after - my_next - 1) if my_after is not None else 0
    window = full_order[my_next + 1:my_after] if my_after is not None else []
    my_picks_left = sum(1 for i in range(idx, len(full_order)) if full_order[i] == me)
    return {"my_next": my_next, "my_after": my_after, "picks_until_me": picks_until_me,
            "decide": (my_next + 1) if my_next is not None else None,
            "following": (my_after + 1) if my_after is not None else None,
            "horizon": horizon, "window": window, "my_picks_left": my_picks_left}


def fetch_picks(ref=None) -> dict:
    d = raw_league_get("mDraftDetail", ref=ref)
    dd = d.get("draftDetail", {})
    # before the draft ESPN returns the whole slate as placeholders with playerId == -1
    picks = [p for p in dd.get("picks", []) if p.get("playerId", 0) > 0]
    return {"in_progress": dd.get("inProgress"), "drafted": dd.get("drafted"), "picks": picks}


def _is_flex(slot: str) -> bool:
    return slot == "OP" or ("/" in slot and slot != "D/ST")


def open_slots(settings: dict, my_pos_counts: dict[str, int]) -> dict[str, int]:
    """How many *starting* slots at each position I still have to fill (flex handled separately)."""
    left = {}
    for slot, n in settings["starters"].items():
        if _is_flex(slot):
            continue
        left[slot] = max(n - my_pos_counts.get(slot, 0), 0)
    return left


def flex_positions(settings: dict) -> set[str]:
    """Which positions the league's flex slots actually accept.

    "OP" is a superflex (QB included); "RB/WR/TE" is not. Reading this off the slot name
    matters: treating QB as flex-eligible in a non-superflex league floats backup QBs up the
    board once the QB slots are full.
    """
    out: set[str] = set()
    for slot in settings["starters"]:
        if not _is_flex(slot):
            continue
        out |= {"QB", "RB", "WR", "TE"} if slot == "OP" else set(slot.split("/"))
    return out


SEASON_WEEKS = 17


def bye_penalty(pos: str, bye, my_roster: pd.DataFrame, settings: dict, vorp: float) -> float:
    """Points lost in the bye week if adding this player leaves the lineup short.

    Priced, not guessed: for each starting slot left unfilled that week, the cost is this
    player's weekly points above replacement — replacement being what you can actually put in
    the slot instead. That self-scales by position. A stacked bye at K costs ~nothing because
    the fallback is just as good; in a 2QB league, where all 20+ usable QBs are rostered and
    the waiver wire is bare, two starting QBs sharing a bye is expensive.

    Only applies once the starting slots at the position are already filled — before that the
    empty slot itself is the problem, and the need weighting handles it.

    On concentration: two QBs sharing a bye and two QBs on different byes lose the same total
    points over a season (two starter-weeks either way). Stacking is still worse, because head
    to head you bank no credit for a comfortable win — one week with two replacement QBs is a
    likely loss, while two weeks one man down are often still wins. So the shortfall is priced
    super-linearly (**1.5), which is the whole reason this penalty distinguishes the two cases.

    Known asymmetry, left in and shown as a column: a position with one starting slot (TE, K,
    D/ST) charges its first player his own bye week; QB/RB/WR do not charge theirs until the
    second. About 5 points against a TE1 next to an RB of equal value.
    """
    need = settings["starters"].get(pos, 0)
    if not need or bye is None or pd.isna(bye) or "bye" not in my_roster.columns:
        return 0.0  # byes ride in on the ADP feed; without them there is nothing to price
    if bye < board_mod.start_week(settings):
        return 0.0  # a bye already played costs the roster nothing
    same = my_roster[my_roster.pos == pos]
    have = len(same) + 1
    if have < need:
        return 0.0
    on_bye = int((same.bye == bye).sum()) + 1
    shortfall = max(need - (have - on_bye), 0)
    if not shortfall:
        return 0.0
    return round(shortfall ** 1.5 * max(vorp, 0.0) / board_mod.weeks_left(settings), 1)


def flex_left(settings: dict, counts: dict[str, int]) -> int:
    """Flex slots still open, after the players I already have fill them.

    Everyone past a position's own starter count spills into the flex, so this has to be
    decremented as the roster fills. Leaving it at the league total made every extra RB look
    startable for the whole draft: in practice draft 3 the board had five RBs and no WR by
    round 6, and at pick 74 a sixth RB (flex weight, 1.1) still outranked a WR filling one of
    two genuinely open WR slots (starter weight, 1.35).
    """
    total = sum(n for s, n in settings["starters"].items() if _is_flex(s))
    spill = sum(max(counts.get(pos, 0) - settings["starters"].get(pos, 0), 0)
                for pos in flex_positions(settings))
    return max(total - spill, 0)


def backups_held(settings: dict, counts: dict[str, int], pos: str) -> int:
    """How many players I already hold at `pos` who cannot start.

    Anyone past the position's own starting slots is a backup, except that a flex-eligible
    position can park that many players in the flex first. (Each flex position is granted the
    whole flex here rather than a share of it — a 5th WR next to four RBs is discounted a
    little less than it should be. Shown, not fixed.)
    """
    flex_room = sum(n for s, n in settings["starters"].items() if _is_flex(s)) \
        if pos in flex_positions(settings) else 0
    return max(counts.get(pos, 0) - settings["starters"].get(pos, 0) - flex_room, 0)


def team_needs(settings: dict, counts: dict[str, int]) -> dict[str, bool]:
    """Whether a team with these position counts still has somewhere to start each position."""
    left = open_slots(settings, counts)
    fl = flex_left(settings, counts)
    fp = flex_positions(settings)
    return {p: left.get(p, 0) > 0 or (p in fp and fl > 0) for p in POSITIONS}


def opponent_demand(settings: dict, window_counts: list[dict[str, int]]) -> dict[str, int]:
    """Picks in the window (between my next pick and the one after) whose team can still start
    the position. Counts picks, not teams: across a snake turn the same team picks twice."""
    out = {p: 0 for p in POSITIONS}
    for counts in window_counts:
        for p, needs in team_needs(settings, counts).items():
            out[p] += int(needs)
    return out


def survival(adp, sd, at_pick: int) -> np.ndarray:
    """P(still on the board when overall pick `at_pick` comes up), from a normal on (adp, stdev).

    A player with no ADP is treated as surviving: the feed only covers ~200 players and the rest
    go undrafted or late. `sd` may be None (fixtures) or NaN (feed has no spread for him).
    """
    a = pd.to_numeric(pd.Series(adp), errors="coerce").to_numpy(float)
    if sd is None:
        s = np.full(len(a), DEFAULT_ADP_SD)
    else:
        s = pd.to_numeric(pd.Series(sd), errors="coerce").to_numpy(float)
        s = np.where(np.isnan(s) | (s <= 0), DEFAULT_ADP_SD, s)
    z = (at_pick - 0.5 - a) / s
    p = 1 - 0.5 * (1 + np.vectorize(math.erf)(z / math.sqrt(2)))
    return np.where(np.isnan(a), 1.0, p).round(2)


def recommend(bd: pd.DataFrame, settings: dict, my_roster: pd.DataFrame, picks_until_me: int,
              my_picks_left: int | None = None, horizon: int | None = None,
              opp_demand: dict[str, int] | None = None, caps: dict[str, int] | None = None) -> dict:
    """The candidate table for the pick I am choosing for.

    picks_until_me: picks by others before that pick (0 = I am on the clock).
    horizon: picks by others between that pick and my following one — what a player must
        survive if I pass on him. Defaults to picks_until_me for callers that predate it.
    opp_demand: per position, how many picks in that horizon belong to teams that can still start
        the position (see `opponent_demand`).
    caps: how many I may roster at each position (a position missing is uncapped); None reads the
        current league's (`strategy.caps`).
    """
    if caps is None:
        caps = strategy.caps()
    avail = bd[~bd.drafted].copy()
    counts = my_roster.pos.value_counts().to_dict() if len(my_roster) else {}
    left = open_slots(settings, counts)
    flex_slots = flex_left(settings, counts)
    flex_pos = flex_positions(settings)
    # Fielding a legal lineup beats every VORP argument. Weighting alone does not guarantee it:
    # the need weight is a flat 1.35, while the VORP gap between the best RB and the best WR
    # left can be far wider than that, so a position can stay unfilled all draft (draft 3
    # finished with eight RBs and no WR at all). Once we have only as many picks left as we
    # have empty starting slots, take from those positions and nowhere else.
    unfilled = sum(left.values())
    if my_picks_left is not None and unfilled and my_picks_left <= unfilled:
        must = avail[avail.pos.isin([p for p, n in left.items() if n > 0])]
        if len(must):
            avail = must.copy()
    weights, need = [], []
    for _, r in avail.iterrows():
        if left.get(r.pos, 0) > 0:
            # The 2QB premium lives in the replacement level (board.py sets it from the league's
            # real QB demand, ~QB28); an extra QB weight on top of that counted it twice and
            # pushed QBs a round or two ahead of the plan.
            w, n = NEED_WEIGHT["starter_open"], "starter"
        elif r.pos in flex_pos and flex_slots:
            w, n = NEED_WEIGHT["flex_open"], "flex"
        else:
            # He cannot start: his position's slots are full and there is no flex spot left
            # (either the flex does not take his position, or the roster has already filled
            # it). VORP prices a *starter*, so it is the wrong currency here — his only value
            # is covering an injury or a bye.
            #
            # This bites hardest at QB in our 2QB league. The QB pool is ~32 deep, so a
            # replacement index of starters+bench lands near its bottom (replacement ~194 pts,
            # about QB28) and every rosterable QB keeps a +50-70 VORP. At 0.9 that still beat
            # a starting WR: in the 2026-09-06 practice draft the recommender took a QB in
            # rounds 6, 7 and 8 with WR2 and TE1 empty, and by pick 74 the top four were QBs.
            #
            # Discounted rather than zeroed (unlike a second K/DST below): a 2QB league starts
            # 20 QBs, so the waiver wire is bare and a QB3 is genuinely worth a late pick. The
            # factor is a judgement call, set so a QB3 lands around rounds 12-14 — where the
            # mock post-mortem in journal/DECISIONS.md put him.
            #
            # Backups have diminishing returns, so the discount compounds with the ones we
            # already hold at the position: the first covers the injury or bye, the fourth
            # covers nothing. Without this, every leftover pick went to whichever position had
            # the largest raw VORP — QB, whose replacement sits at QB28 — and draft 4 replayed
            # into a roster with six quarterbacks on it.
            w, n = NEED_WEIGHT["unstartable"] / (1 + backups_held(settings, counts, r.pos)), "bench"
        weights.append(w)
        need.append(n)
    avail["need"] = need
    # Weight only the positive part: below replacement, raw VORP is already the honest
    # cross-position ranking, and scaling it would penalise the positions we most need.
    # Clipping instead (the old behaviour) flattened every such player to 0, leaving the
    # tie-break to raw projection — which is not comparable across positions and floated
    # backup QBs and spare defenses above usable starters late in the draft.
    vorp = avail.vorp.to_numpy()
    avail["score"] = np.where(vorp > 0, vorp * weights, vorp).round(1)
    # Bye-week collisions among players who would start. Priced against the best replacement
    # still on the board, so it barely moves K/DST and bites hardest where the pool is thin.
    avail["bye_risk"] = [
        bye_penalty(r.pos, r.bye, my_roster, settings, r.vorp) for r in avail.itertuples()
    ]
    avail["score"] = (avail.score - avail.bye_risk).round(1)
    # A K/DST whose slot is filled is unusable however good its VORP looks: you only start one.
    filled = avail.pos.isin(["K", "D/ST"]) & avail.pos.map(lambda p: left.get(p, 0) == 0)
    avail.loc[filled, "score"] = -999.0
    # Nor is one worth a pick before the last rounds. Discounting was not enough: the weight is
    # a multiplier, and by round 12 every skill player left scores 0.0, so 0.2 x a defense's 18
    # VORP still floated to the top — draft 6 spent rounds 12 and 13 on a kicker and a defense.
    # The gap between the best D/ST and the tenth is 23 points, about 1.3 a week, and the
    # eleventh is on waivers all season; a round-13 dart on a role player is worth more. So they
    # are off the board entirely until the run-in, which is what the strategy has always said.
    if my_picks_left is not None and my_picks_left > unfilled:
        avail.loc[avail.pos.isin(["K", "D/ST"]), "score"] = -999.0
    # A second backup at a position that cannot flex is dead weight: with a QB3 already
    # stashed, a QB4 can only ever be the third-string quarterback of a two-quarterback
    # lineup. Left merely discounted, he still beat every dart, because a dart's upside is
    # invisible to VORP (it scores 0.0 by construction) while his is not.
    extra_backup = [p not in flex_pos and backups_held(settings, counts, p) >= 1
                    for p in avail.pos]
    avail.loc[extra_backup, "score"] = -999.0
    # The league's caps (league data: `ffl.leagues caps`, seeded from the strategy's suggested
    # caps). TE is why they exist: tight ends dodge the rule above by being flex-eligible. Once the
    # starters are set, a leftover TE keeps a small positive VORP (the pool is thin, so replacement
    # sits at TE14) while every RB and WR left has gone negative, and a small positive number beats
    # a negative one however hard it is discounted: a full 17-round replay ended with six or seven
    # tight ends from every slot. A roster rule, not a weight. The two rules above stay beside the
    # caps: they follow the lineup (a second K/D/ST, a second non-flex backup) where a cap is a
    # fixed count, so in the home league they bind exactly where its QB 3, K 1 and D/ST 1 do.
    at_cap = [counts.get(p, 0) >= caps.get(p, 99) for p in avail.pos]
    avail.loc[at_cap, "score"] = -999.0
    # Late on, everything left is at or below replacement and scores collapse to a tie at 0 or
    # below. Raw projection then decided the order — and it is not comparable across positions,
    # so a QB always won and the last picks of a replayed draft went QB, QB, QB. Break those
    # ties towards positions we are not already stacked at before falling back to projection.
    avail["depth"] = [backups_held(settings, counts, p) for p in avail.pos]

    # Survival. Two horizons matter. `decide` is the pick I am choosing for; a player with ADP
    # before it may not be there at all (gone_before_me — only a warning, the table is still
    # built for that pick). `following` is my pick after that; a player I pass on at `decide`
    # has to survive every other team's pick in between to be an option then. The old code
    # measured survival only to the next pick, with a two-pick fudge, so on the clock it saw no
    # risk at all and one pick out it flagged only players whose ADP had already passed —
    # practice draft 7's Evans fix worked because he was a faller, and would not have otherwise.
    current = int(bd.drafted.sum()) + 1
    decide = current + picks_until_me
    if horizon is None:
        horizon = picks_until_me
    following = decide + horizon + 1
    sd = avail["adp_stdev"] if "adp_stdev" in avail.columns else None
    avail["gone_before_me"] = (survival(avail.adp, sd, decide) < 0.5) if picks_until_me > 0 \
        else False
    avail["survive_prob"] = survival(avail.adp, sd, following) if horizon > 0 else 1.0
    avail["likely_gone"] = avail.survive_prob < 0.5
    # A QB or TE only goes to a team in the window that can still start one; ADP alone would
    # say six QBs vanish in a window where two teams need one.
    if opp_demand:
        for pos in DEMAND_CAPPED:
            allowed = int(opp_demand.get(pos, 0)) + 1  # one for a best-available reach
            gone = avail[(avail.pos == pos) & avail.likely_gone].sort_values("adp")
            if len(gone) > allowed:
                avail.loc[gone.index[allowed:], "likely_gone"] = False
    avail["opp_demand"] = avail.pos.map(lambda p: int((opp_demand or {}).get(p, 0)))
    # The cost of waiting: for each position with an open starting slot, the best available
    # now minus the best that should survive to my following pick (or the bottom of the
    # position if nobody will), added to the whole position. Priced rather than forced, so a
    # genuinely elite player who slid is still visible; a hard filter would have blanked the
    # board if the ADP feed ever made a position look falsely doomed. VORP alone cannot see
    # this: replacement level is league-wide and *rises* as a position drains, so a run makes
    # the players still available look worthless exactly when they are scarcest (draft 7,
    # slot 10, lost 36 projected points to it at WR).
    #
    # "Usable" is VORP >= 0, not > 0: replacement is measured against the *available* pool, so
    # once a position is drained past its league-wide demand the best man left sits at exactly
    # 0.0. He can still start, which beats a bench player who cannot.
    avail["urgency"] = 0.0
    if horizon > 0:
        for pos, n in left.items():
            if n <= 0:
                continue
            pool = avail[avail.pos == pos]
            if not len(pool):
                continue
            surviving = pool[~pool.likely_gone]
            best_later = surviving.vorp.max() if len(surviving) else pool.vorp.min()
            cost = round(max(pool.vorp.max() - best_later, 0.0), 1)
            avail.loc[avail.pos == pos, "urgency"] = cost
    avail["score"] = (avail.score + avail.urgency).round(1)
    # How far the two projection sources disagree — a large gap means the average is hiding a
    # real question (injury, role), not a number to trust. Uncertainty, not upside: there is no
    # ceiling feed in this pipeline.
    if {"proj_espn", "proj_sleeper"} <= set(avail.columns):
        avail["spread"] = (avail.proj_espn - avail.proj_sleeper).round(1)
    else:
        avail["spread"] = np.nan
    avail = avail.sort_values(["score", "depth", "proj"], ascending=[False, True, False])
    cols = ["player_id", "name", "pos", "team", "bye", "proj", "vorp", "tier", "adp", "survive_prob",
            "likely_gone", "gone_before_me", "score", "urgency", "need", "bye_risk", "opp_demand",
            "spread", "injury"]
    cols = [c for c in cols if c in avail.columns]
    # A player on injured reserve cannot be drafted into the IR slot, but he can be drafted to
    # the bench and moved there afterwards, which hands the roster spot back. In the last rounds
    # that beats a flier when he is good and expected back. His projection is already docked for
    # the games he misses, so he never surfaces on score — list him separately.
    ir = avail[avail.injury == "INJURY_RESERVE"] if "injury" in avail.columns else avail.head(0)
    return {
        "needs": left, "flex_open": flex_slots,
        "decide_pick": decide, "following_pick": following, "horizon": horizon,
        "opp_demand": opp_demand or {},
        "top": avail.head(10)[cols].to_dict("records"),
        "by_pos": {p: avail[avail.pos == p].head(4)[cols].to_dict("records") for p in POSITIONS},
        "survivors": avail[~avail.likely_gone].head(8)[cols].to_dict("records"),
        "ir_stash": ir.nlargest(4, "proj")[cols].to_dict("records") if len(ir) else [],
    }


def roster_counts(picks: list[dict], id_to_row: pd.DataFrame) -> dict[int, dict[str, int]]:
    """Position counts per team from the pick list. Backfilled picks (teamId 0) are skipped."""
    out: dict[int, dict[str, int]] = {}
    for p in picks:
        t, pid = p.get("teamId"), p["playerId"]
        if not t or pid not in id_to_row.index:
            continue
        pos = id_to_row.loc[pid]["pos"]
        out.setdefault(t, {})
        out[t][pos] = out[t].get(pos, 0) + 1
    return out


def candidate_table(rows: list[dict], n: int = 10) -> str:
    df = pd.DataFrame(rows[:n])
    if not len(df):
        return "(nothing on the board)"
    df["inj"] = df.injury.map(lambda s: INJURY_SHORT.get(s, s if isinstance(s, str) else ""))
    df["surv"] = df.survive_prob.map(lambda p: f"{p:.0%}")
    df["gone"] = np.where(df.gone_before_me, "!", "")
    cols = [c for c in ["gone", "name", "pos", "proj", "vorp", "tier", "adp", "surv", "score", "urgency",
                        "need", "bye", "bye_risk", "spread", "opp_demand", "inj", "player_id"]
            if c in df.columns]
    return df[cols].to_string(index=False)


class Watcher:
    """Board state + logging for one league (None: the current one), driven by whatever pick feed it is handed."""

    def __init__(self, ref=None):
        self.ref = ref or leagues.current()
        self.settings, self.players, self.adp = board_mod.load_inputs(self.ref)
        self.me = my_team_id(self.settings, self.ref)
        # the league's caps and QB pace, read once: every tick (one a second live) would read the DB, and a
        # failed lookup inside the rollout thread would be a SystemExit there, not a line on screen
        self.caps = strategy.caps(self.ref)
        self.qb_pace = strategy.qb_pace(self.ref)
        self.teams = {t["id"]: t for t in self.settings["teams"]}
        self.base_order = pick_order(self.settings)
        self.order = list(self.base_order)
        self.rounds = self.settings["roster_size"]
        self.full_order = snake_order(self.order, self.rounds)
        self.id_to_row = self.players.set_index("player_id")
        self.seen = 0
        self._written: dict | None = None  # the last state saved to draft_states, less its stamp
        # FFL_ROLLOUT=1: the Monte Carlo second opinion (ffl/rollout.py) is printed under the
        # table for the heads-up and on-clock picks. Off, nothing here changes.
        from . import rollout as rollout_mod  # here, not at the top: rollout imports this module
        self.rollout_mod = rollout_mod if rollout_mod.enabled() else None
        self._roll_gen = 0

    def my_slot(self) -> int | str:
        return self.order.index(self.me) + 1 if self.me in self.order else "?"

    def banner(self, source: str):
        print(f"Watching league {self.ref.key} via {source} — I am team {self.me} "
              f"({self.teams[self.me]['name']}), slot {self.my_slot()} of {len(self.order)}", flush=True)
        if note := board_mod.banner(self.settings):
            print(note, flush=True)
        if order_is_placeholder(self.settings):
            print("!! Draft order is randomized at draft start and settings.json still holds the "
                  "placeholder 1..n. The slot above is a guess until the room shows the order: set "
                  "FFL_PICK_ORDER=<team ids in order> (or re-run ffl.settings) and restart, or let the "
                  "first-round picks correct it as they land.", flush=True)

    def update_order(self, picks: list[dict], on_clock: int | None, current: int):
        learned = learn_order(self.base_order, picks, on_clock, current)
        if learned != self.order:
            self.order = learned
            self.full_order = snake_order(self.order, self.rounds)
            print(f"[{datetime.now():%H:%M:%S}] pick order from the room: {self.order} "
                  f"-> my slot {self.my_slot()}", flush=True)

    def tick(self, picks: list[dict], on_clock: int | None = None, done: bool = False,
             offset: int = 0) -> dict:
        """Ingest the current pick list; log anything new; return (and persist) full state."""
        drafted_ids = {p["playerId"] for p in picks}
        bd = board_mod.build(self.settings, self.players, self.adp, drafted_ids=drafted_ids)
        fresh = len(picks) > self.seen
        if fresh:
            pre = board_mod.build(self.settings, self.players, self.adp,
                                  drafted_ids={p["playerId"] for p in picks[:self.seen]})
            pre_rank = pre.set_index("player_id").board_rank
            adp_by_id = bd.set_index("player_id").adp
            new_rows = []
            for p in picks[self.seen:]:
                pid = p["playerId"]
                row = self.id_to_row.loc[pid] if pid in self.id_to_row.index else None
                name = row["name"] if row is not None else f"id{pid}"
                pos = row["pos"] if row is not None else "?"
                br, a = pre_rank.get(pid), adp_by_id.get(pid)
                tag = " <== ME" if p["teamId"] == self.me else ""
                print(f"[{datetime.now():%H:%M:%S}] {p['roundId']}.{p['roundPickNumber']:02d} (#{p['overallPickNumber']}) "
                      f"{self.teams.get(p['teamId'],{}).get('abbrev','?'):>5} -> {name} ({pos}) "
                      f"board#{br if pd.notna(br) else '-'} adp {a if pd.notna(a) else '-'}{tag}", flush=True)
                new_rows.append({
                    "overall": p["overallPickNumber"], "round": p["roundId"], "pick": p["roundPickNumber"],
                    "team_id": p["teamId"], "team": self.teams.get(p["teamId"], {}).get("name"),
                    "player_id": pid, "player": name, "pos": pos,
                    "nfl": row["team"] if row is not None else None,
                    "adp_2qb": float(a) if pd.notna(a) else None,
                    "board_rank_at_pick": float(br) if pd.notna(br) else None,
                    "proj": float(row["proj"]) if row is not None else None,
                    "ts": datetime.now().isoformat(timespec="seconds")})
            db.put_picks(new_rows, ref=self.ref)
            self.seen = len(picks)

        current = len(picks) + offset + 1  # offset = picks made before we attached
        self.update_order(picks, on_clock, current)
        if on_clock is None:  # no live feed: infer from the snake order
            on_clock = self.full_order[current - 1] if current - 1 < len(self.full_order) else None
        tm = turn_math(self.full_order, self.me, current)
        my_roster = bd[bd.drafted & bd.player_id.isin({p["playerId"] for p in picks if p["teamId"] == self.me})]
        by_team = roster_counts(picks, self.id_to_row)
        opp = opponent_demand(self.settings, [by_team.get(t, {}) for t in tm["window"]])
        rec = recommend(bd, self.settings, my_roster, max(tm["picks_until_me"], 0), tm["my_picks_left"],
                        horizon=tm["horizon"], opp_demand=opp, caps=self.caps)
        recent = [self.id_to_row.loc[p["playerId"]]["pos"] for p in picks[-6:] if p["playerId"] in self.id_to_row.index]
        runs = {pos: recent.count(pos) for pos in set(recent) if recent.count(pos) >= 3}
        state = {
            "ts": datetime.now().isoformat(timespec="seconds"), "drafted": done,
            "picks_made": len(picks), "current_pick": current, "round": (current - 1) // len(self.order) + 1,
            "order": self.order, "my_slot": self.my_slot(),
            "on_clock": self.teams.get(on_clock, {}).get("name") if on_clock else None, "on_clock_is_me": on_clock == self.me,
            "picks_until_me": tm["picks_until_me"], "my_next_overall": tm["decide"],
            "my_following_overall": tm["following"], "horizon": tm["horizon"], "my_picks_left": tm["my_picks_left"],
            "window": [self.teams.get(t, {}).get("abbrev", t) for t in tm["window"]],
            "opp_demand": opp,
            "my_roster": my_roster[["name", "pos", "team", "bye", "proj", "tier"]].to_dict("records"),
            "runs": runs, "recommend": rec,
            "rosters": {self.teams[t]["name"]: [self.id_to_row.loc[p["playerId"]]["name"] + f" ({self.id_to_row.loc[p['playerId']]['pos']})"
                                                for p in picks if p["teamId"] == t and p["playerId"] in self.id_to_row.index] for t in self.teams},
        }
        self.save_state(state)
        state["_fresh"] = fresh
        state["_rec"] = rec
        state["_rollout_inputs"] = (bd, tm, by_team, my_roster)
        if fresh:
            self._roll_gen += 1  # any rollout still running was computed for a board that has moved
        return state

    def safe_tick(self, *args, **kwargs) -> dict | None:
        """tick(), but a DB error (the live DB busy past its timeout) is one line on screen and None, never the end
        of the watcher mid-draft. The picks not yet logged stay unseen, so the next tick logs them again."""
        try:
            return self.tick(*args, **kwargs)
        except db.DBError as e:
            print(f"[{datetime.now():%H:%M:%S}] DB error, picks not logged yet ({e.__class__.__name__}: {e}); "
                  "retrying next tick", flush=True)
            return None

    def save_state(self, state: dict):
        """Write the state to the league's draft_states row when it changed (its clock stamp aside): a tick
        comes about once a second, against the live DB the API writes too. A failed write is one line on
        screen, never the end of the watcher: the next tick sees a state not yet written and tries again."""
        doc = json.loads(json.dumps(state, default=str))  # plain JSON, numpy scalars and the like as strings
        same = {k: v for k, v in doc.items() if k != "ts"}
        if same == self._written:
            return
        try:
            db.put_draft_state(self.ref, doc)
        except db.DBError as e:
            print(f"[{datetime.now():%H:%M:%S}] draft state not saved ({e.__class__.__name__}); retrying next tick",
                  flush=True)
            return
        self._written = same

    def print_board(self, state: dict):
        rec = state["_rec"]
        pum = state["picks_until_me"]
        if state["on_clock_is_me"]:
            flag = " *** ON THE CLOCK ***"
        elif pum == 2:
            flag = " *** HEADS-UP: two picks out — decide now ***"
        elif pum == 1:
            flag = " *** next pick is mine ***"
        else:
            flag = ""
        print(f"--- pick #{state['current_pick']} R{state['round']} | on clock: {state['on_clock']}"
              f"{' (ME)' if state['on_clock_is_me'] else ''} | mine: #{state['my_next_overall']} in {pum}, "
              f"then #{state['my_following_overall']} ({state['horizon']} picks between) "
              f"| needs {rec['needs']} | opp demand {state['opp_demand']} | runs {state['runs']}{flag}", flush=True)
        print(candidate_table(rec["top"]), flush=True)
        # The top ten hides whole positions once they sit at replacement (late in the draft no
        # WR makes it, and the IR stash never does), which is how three already-drafted WRs got
        # named at pick 101 of practice draft 9. So the on-clock print answers "who is left at
        # each position" by itself.
        by_pos = "  ".join(
            f"{pos}: " + ", ".join(f"{r['name']} ({r['vorp']:.0f}, {r['survive_prob']:.0%})" for r in rows[:3])
            for pos, rows in rec["by_pos"].items() if rows)
        print(f"best left  {by_pos}", flush=True)
        if rec["ir_stash"]:
            print("IR stash   " + ", ".join(f"{r['name']} ({r['proj']:.0f})" for r in rec["ir_stash"]), flush=True)
        if self.rollout_mod is not None and pum in (0, 2):  # the heads-up and the clock, not both and the pick between
            self.rollout_async(state)

    def rollout_async(self, state: dict):
        """Print the rollout under the table once it is done, without holding the table up.

        Runs in a thread: the table is on screen at once and the rollout lands a few seconds
        later, labelled with the pick it was computed for. If another pick has landed by then
        the lines are printed anyway but marked stale, since the roster they assumed has moved.
        A failure prints one line and changes nothing else.
        """
        bd, tm, by_team, my_roster = state["_rollout_inputs"]
        rec, decide = state["_rec"], state["my_next_overall"]
        gen = self._roll_gen  # advanced by tick() on every fresh pick

        def work():
            try:
                res = self.rollout_mod.from_state(bd, self.settings, self.full_order, self.me, tm,
                                                  by_team, my_roster, rec, caps=self.caps, qb_pace=self.qb_pace)
                lines = self.rollout_mod.rollout_lines(res, rec)
            except (Exception, SystemExit) as e:  # never let the second opinion take the first one down
                lines = [f"rollout    failed: {e!r}"]
            stale = "" if gen == self._roll_gen else " (STALE: a pick landed since)"
            for line in lines:
                print(f"{line}   <- for pick #{decide}{stale}", flush=True)

        threading.Thread(target=work, daemon=True, name=f"rollout-{decide}").start()


def numbered(picks: list[dict], n_teams: int, offset: int = 0) -> list[dict]:
    """Socket picks arrive in draft order; derive round / round-pick numbers.

    `offset` is how many picks were made before we attached — without it every number is
    shifted and the board thinks those players are still available.
    """
    out = []
    for i, p in enumerate(picks):
        j = i + offset
        out.append({**p, "overallPickNumber": j + 1, "roundId": j // n_teams + 1,
                    "roundPickNumber": j % n_teams + 1})
    return out


def run_socket():
    """Live mode: read picks off the draft room websocket via the open Chrome tab."""
    from .draft_socket import DraftFeed, FeedStore

    w = Watcher()
    n_teams = len(w.order)
    total = n_teams * w.rounds
    with DraftFeed(w.ref.provider_id, store=FeedStore(w.ref)) as feed:
        print(f"Attached to {feed.url}", flush=True)
        w.banner("draft-room socket")
        if feed.picks:
            print(f"Resuming with {len(feed.picks)} picks from the DB's draft feed", flush=True)
        if feed.sync_offset():
            missed = feed.offset
            got = feed.backfill({str(n).lower(): int(i) for n, i in
                                 zip(w.players.name, w.players.player_id)})
            print(f"!! ATTACHED LATE: {missed} pick(s) made before we attached; "
                  f"recovered {len(got)} from the room's Picks rail.", flush=True)
            if feed.offset:
                print(f"   {feed.offset} still unaccounted for — those players will show as "
                      f"available. Cross-check the room before trusting a recommendation.", flush=True)
        state = w.safe_tick(numbered(feed.picks, n_teams, feed.offset), feed.on_clock_now(), offset=feed.offset)
        if state is not None:
            w.print_board(state)
        while True:
            if not feed.pump(1000):  # CDP link dropped (tab reloaded, Chrome restarted)
                print(f"[{datetime.now():%H:%M:%S}] lost the draft tab; reattaching…", flush=True)
                time.sleep(2)
                try:
                    print(f"  reattached to {feed.reconnect()}", flush=True)
                    print("  NOTE: picks made while detached were missed — check the room's "
                          "Pick History against the draft_picks table", flush=True)
                except Exception as e:
                    print(f"  reattach failed: {e}", flush=True)
                continue
            state = w.safe_tick(numbered(feed.picks, n_teams, feed.offset), feed.on_clock_now(),
                                done=len(feed.picks) + feed.offset >= total, offset=feed.offset)
            if state is None:
                continue
            if state["_fresh"]:
                w.print_board(state)
            if state["drafted"]:
                print("Draft complete.", flush=True)
                break


def run(once: bool = False):
    """REST mode: only good for pre-draft sanity checks and post-draft recaps.

    ESPN leaves mDraftDetail at playerId -1 for the whole live draft, so this sees nothing
    while picks are actually happening. Use run_socket() during the draft.
    """
    w = Watcher()
    w.banner("REST mDraftDetail (pre/post-draft only)")
    printed = False
    while True:
        try:
            d = fetch_picks(w.ref)
        except Exception as e:  # network hiccup: keep polling
            print(f"[{datetime.now():%H:%M:%S}] fetch error: {e}", flush=True)
            time.sleep(POLL_S)
            continue
        picks = sorted(d["picks"], key=lambda p: p.get("overallPickNumber", 0))
        state = w.safe_tick(picks, done=bool(d["drafted"]))
        if state is None:
            time.sleep(POLL_S)
            continue
        if state["_fresh"] or once or not printed:
            w.print_board(state)
            printed = True
        if once:
            break
        if state["drafted"]:
            print("Draft complete.", flush=True)
            break
        time.sleep(POLL_S)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "socket"
    if cmd == "once":
        run(once=True)
    elif cmd == "rest":
        run()
    else:
        run_socket()
