"""Dry-run the draft from a slot, through the live recommender.

    uv run python -m ffl.simulate 7               # slot 7, all rounds
    uv run python -m ffl.simulate 7 --rounds 6    # first six rounds
    uv run python -m ffl.simulate all --rounds 6  # every slot, one line per pick of mine

Opponents take the best player left by 2QB ADP; I take the board's top candidate. That makes it
a check on our own logic — does the lineup fill, do the QBs land in the window of the league's strategy (`uv run python -m ffl.strategy show`), does
the heads-up (two picks out) agree with the on-clock table — not a prediction of who will be
there. Reads the same inputs as the watcher, for the current league (FFL_LEAGUE, else the home
league); writes nothing.
"""
from __future__ import annotations

import argparse

import pandas as pd

from . import board as board_mod
from . import leagues, strategy
from .draft_watch import (my_team_id, opponent_demand, recommend, roster_counts, snake_order,
                          turn_math)


def order_for_slot(settings: dict, me: int, slot: int) -> list[int]:
    others = [t["id"] for t in settings["teams"] if t["id"] != me]
    return others[:slot - 1] + [me] + others[slot - 1:]


def run_slot(settings, players, adp, me: int, slot: int, rounds: int, verbose: bool = True,
             rollout_sims: int = 100, pick_by: str = "score", caps: dict[str, int] | None = None,
             qb_pace: float | None = None) -> dict:
    """caps / qb_pace: the league's (None: none capped, no pace); main reads them once per run."""
    from . import rollout as _ro
    rollout_mod = _ro if _ro.enabled() else None
    caps, qb_pace = caps or {}, qb_pace or 0.0
    order = order_for_slot(settings, me, slot)
    n = len(order)
    full = snake_order(order, settings["roster_size"])
    id_to_row = players.set_index("player_id")
    picks: list[dict] = []
    mine: list[dict] = []
    disagreements = 0

    def state(current):
        bd = board_mod.build(settings, players, adp, drafted_ids={p["playerId"] for p in picks})
        roster = bd[bd.player_id.isin({p["playerId"] for p in picks if p["teamId"] == me})]
        tm = turn_math(full, me, current)
        by_team = roster_counts(picks, id_to_row)
        opp = opponent_demand(settings, [by_team.get(t, {}) for t in tm["window"]])
        rec = recommend(bd, settings, roster, max(tm["picks_until_me"], 0), tm["my_picks_left"],
                        horizon=tm["horizon"], opp_demand=opp, caps=caps)
        return bd, roster, rec, tm, by_team

    heads_up = None  # (pick it was for, top-3 [(player_id, name)])
    for p in range(1, rounds * n + 1):
        team = full[p - 1]
        bd, roster, rec, tm, by_team = state(p)
        if tm["picks_until_me"] == 2:
            heads_up = (p + 2, [(int(r["player_id"]), r["name"]) for r in rec["top"][:3]])
        if team == me:
            top = rec["top"][0]
            res = None
            if rollout_mod is not None:
                res = rollout_mod.from_state(bd, settings, full, me, tm, by_team, roster, rec,
                                             n_sims=rollout_sims, time_budget_s=8.0, seed=p, caps=caps,
                                             qb_pace=qb_pace)
                if pick_by == "rollout" and res["rows"]:
                    # let the rollout draft, unfiltered by the hard rules: the test of whether
                    # it reaches the strategy's deadlines on its own
                    lead = res["rows"][0]
                    top = next((r for r in rec["top"] if int(r["player_id"]) == lead["player_id"]), None) or {
                        **{k: lead.get(k) for k in ("player_id", "name", "pos", "proj")},
                        "score": float("nan"), "urgency": float("nan"), "survive_prob": float("nan")}
            # A real disagreement is the heads-up's man still being there and the on-clock
            # table preferring someone else. His being drafted in between is expected — that
            # is what the decision tree in the heads-up is for.
            taken = {q["playerId"] for q in picks}
            still_there = heads_up is not None and heads_up[0] == p and heads_up[1][0][0] not in taken
            agree = not still_there or heads_up[1][0][0] == int(top["player_id"])
            if not agree:
                disagreements += 1
            picks.append({"teamId": me, "playerId": int(top["player_id"]), "overallPickNumber": p})
            mine.append({"pick": p, "round": (p - 1) // n + 1, **top, "agree": agree})
            if verbose:
                alts = " | ".join(f"{r['name']} {r['pos']} s{r['score']} u{r['urgency']} {r['survive_prob']:.0%}"
                                  for r in rec["top"][1:4])
                hu = "" if agree else f"   !! heads-up said {heads_up[1][0][1]}"
                print(f"#{p:>3} R{(p - 1) // n + 1:<2} {top['name']:<22} {top['pos']:<4} score {top['score']:>6} "
                      f"urg {top['urgency']:>5} | then: {alts}{hu}")
                if res is not None:
                    # by default the pick is made by score and the rollout is shown beside it,
                    # so a whole draft's disagreements can be read in one pass
                    print("        " + rollout_mod.verdict_line(res, int(top["player_id"])))
        else:
            avail = bd[~bd.drafted]
            with_adp = avail.dropna(subset=["adp"]).copy()
            if qb_pace:  # FFL_QB_PACE: this room takes its QBs that many picks earlier than the feed
                with_adp.loc[with_adp.pos == "QB", "adp"] = (with_adp.loc[with_adp.pos == "QB", "adp"] - qb_pace).clip(lower=1.0)
            with_adp = with_adp.sort_values("adp")
            pick = with_adp.iloc[0] if len(with_adp) else avail.sort_values("proj", ascending=False).iloc[0]
            picks.append({"teamId": team, "playerId": int(pick.player_id), "overallPickNumber": p})
    pos_counts = pd.Series([m["pos"] for m in mine]).value_counts().to_dict()
    # The hard rule in the league's strategy (`uv run python -m ffl.strategy show`): two of the top-twenty QBs. The twentieth's ADP is where the
    # last of them comes off the board, so a second QB taken after it is a rule violation.
    qb_pool = board_mod.build(settings, players, adp)
    qb20 = qb_pool[qb_pool.pos == "QB"].nlargest(20, "proj").adp.max()
    qb_picks = [m["pick"] for m in mine if m["pos"] == "QB"]
    warnings = []
    if len(qb_picks) < 2 or qb_picks[1] > qb20:
        warnings.append(f"QB2 at {qb_picks[1] if len(qb_picks) > 1 else 'never'} is past the "
                        f"twentieth QB's ADP ({qb20:.0f})")
    for pos, cap in caps.items():
        if pos_counts.get(pos, 0) > cap:
            warnings.append(f"{pos_counts[pos]} {pos}s rostered (cap {cap})")
    return {"slot": slot, "mine": mine, "pos_counts": pos_counts, "disagreements": disagreements,
            "warnings": warnings}


def short(name: str) -> str:
    return name if name.endswith("D/ST") else name.split()[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slot", help="1..n, or 'all'")
    ap.add_argument("--rounds", type=int, default=None)
    ap.add_argument("--pick-by", choices=["score", "rollout"], default="score",
                    help="who makes my picks: score (default) or the rollout (needs FFL_ROLLOUT=1)")
    a = ap.parse_args()
    ref = leagues.acting()
    settings, players, adp = board_mod.load_inputs(ref)
    me = my_team_id(settings, ref)
    caps, qb_pace = strategy.caps(ref), strategy.qb_pace(ref)
    n = settings["team_count"]
    rounds = a.rounds or settings["roster_size"]
    slots = range(1, n + 1) if a.slot == "all" else [int(a.slot)]
    for slot in slots:
        print(f"\n=== SLOT {slot} ({rounds} rounds) ===")
        res = run_slot(settings, players, adp, me, slot, rounds, verbose=(a.slot != "all"), pick_by=a.pick_by,
                       caps=caps, qb_pace=qb_pace)
        if a.slot == "all":
            print("  " + " | ".join(f"#{m['pick']} {short(m['name'])} {m['pos']}" for m in res["mine"]))
        qbs = [m["pick"] for m in res["mine"] if m["pos"] == "QB"]
        print(f"  roster: {res['pos_counts']} | QB picks at {qbs} | heads-up/on-clock disagreements: {res['disagreements']}")
        for w in res["warnings"]:
            print(f"  !! {w}")


if __name__ == "__main__":
    main()
