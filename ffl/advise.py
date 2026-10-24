"""What to do on the clock, recomputed from the raw pick feed.

    uv run python -m ffl.advise            # top 8
    uv run python -m ffl.advise 12         # deeper list
    uv run python -m ffl.advise --made 3   # the watcher attached 3 picks late

Deliberately rebuilds the board from `data/socket_picks.json` (the watcher's raw pick log)
instead of reading its `draft_state.json`. That way advice always reflects the current code
and keeps working even if the watcher process has died or gone stale mid-draft. The pick math,
opponent model and candidate table are the watcher's own functions, so the two cannot drift.
"""
from __future__ import annotations

import argparse
import json

from . import board as board_mod
from .config import DATA
from .draft_watch import (candidate_table, learn_order, my_team_id, numbered, opponent_demand,
                          order_is_placeholder, pick_order, recommend, roster_counts, snake_order,
                          turn_math)


def load_picks() -> list[dict]:
    path = DATA / "socket_picks.json"
    if not path.exists():
        raise SystemExit(f"no {path} yet — is the watcher attached to the draft room?")
    return json.loads(path.read_text())


def main(n: int = 8, made: int = 0):
    settings, players, adp = board_mod.load_inputs()
    me = my_team_id(settings)
    base = pick_order(settings)
    picks = numbered(load_picks(), len(base), made)
    order = learn_order(base, picks)
    full_order = snake_order(order, settings["roster_size"])
    teams = {t["id"]: t for t in settings["teams"]}

    bd = board_mod.build(settings, players, adp, drafted_ids={p["playerId"] for p in picks})
    my_roster = bd[bd.player_id.isin({p["playerId"] for p in picks if p["teamId"] == me})]
    current = len(picks) + made + 1
    tm = turn_math(full_order, me, current)
    by_team = roster_counts(picks, players.set_index("player_id"))
    opp = opponent_demand(settings, [by_team.get(t, {}) for t in tm["window"]])
    rec = recommend(bd, settings, my_roster, max(tm["picks_until_me"], 0), tm["my_picks_left"],
                    horizon=tm["horizon"], opp_demand=opp)

    on_clock = full_order[current - 1] if current - 1 < len(full_order) else None
    tag = " <-- YOU" if on_clock == me else ""
    slot = order.index(me) + 1 if me in order else "?"
    print(f"pick #{current} R{(current - 1) // len(order) + 1} | on clock: "
          f"{teams.get(on_clock, {}).get('name', '?')}{tag} | slot {slot} | your next: "
          f"#{tm['decide']} (in {tm['picks_until_me']}), then #{tm['following']} "
          f"({tm['horizon']} picks between)")
    if note := board_mod.banner(settings):
        print(note)
    if order_is_placeholder(settings) and len(picks) < len(order):
        print("!! pick order not confirmed (settings.json placeholder; round 1 not complete) — "
              "set FFL_PICK_ORDER or re-run ffl.settings")
    print("roster:", ", ".join(f"{r['name']} ({r['pos']})" for r in my_roster.to_dict("records")) or "empty")
    print("needs:", rec["needs"], "| flex open:", rec["flex_open"], "| opp demand:", opp)

    print("\nCANDIDATES  (score is the default order; decide on the columns)")
    print(candidate_table(rec["top"], n))
    print("\nBEST AT EACH POSITION  (vorp, survive%)")
    for pos, rows in rec["by_pos"].items():
        if rows:
            print(f"  {pos:<4} " + " | ".join(f"{r['name']} ({r['vorp']}, {r['survive_prob']:.0%})" for r in rows[:3]))
    print("\nSHOULD SURVIVE TO YOUR FOLLOWING PICK")
    for r in rec["survivors"][:6]:
        print(f"  {r['name']:<22} {r['pos']:<4} vorp {r['vorp']:>6}  adp {r['adp']}  {r['survive_prob']:.0%}")
    if rec["ir_stash"]:
        print("\nIR STASH (draft to bench, move to IR after the draft)")
        for r in rec["ir_stash"]:
            print(f"  {r['name']:<22} {r['pos']:<4} proj {r['proj']:>6}  adp {r['adp']}")
    from . import rollout as rollout_mod
    if rollout_mod.enabled() and tm["decide"] is not None:
        print("\nROLLOUT  (season lineup points if I take him now and the draft plays out to ADP; gap to the leader)")
        res = rollout_mod.from_state(bd, settings, full_order, me, tm, by_team, my_roster, rec)
        top = res["rows"][0]["roll"] if res["rows"] else 0.0
        for r in res["rows"]:
            nxt = f"then {r['next_pos']} ~{r['next_proj']:.0f}" if r["next_pos"] else ""
            print(f"  {r['name']:<22} {r['pos']:<4} {r['roll'] - top:>+6.1f} ±{r['gap_se']:<4} {nxt}")
        for line in rollout_mod.rollout_lines(res, rec)[1:]:
            print("  " + line)
        print(f"  [n={res['n_sims']}, {res['elapsed']}s]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=8)
    ap.add_argument("--made", type=int, default=0, help="picks made before the watcher attached")
    a = ap.parse_args()
    main(a.n, a.made)
