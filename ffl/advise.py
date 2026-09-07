"""What to do on the clock, recomputed from the raw pick feed.

    uv run python -m ffl.advise            # top 8
    uv run python -m ffl.advise 12         # deeper list

Deliberately rebuilds the board from `data/socket_picks.json` (the watcher's raw pick log)
instead of reading its `draft_state.json`. That way advice always reflects the current code
and keeps working even if the watcher process has died or gone stale mid-draft.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from . import board as board_mod
from .config import DATA
from .draft_watch import my_team_id, recommend, snake_order


def load_picks() -> list[dict]:
    path = DATA / "socket_picks.json"
    if not path.exists():
        raise SystemExit(f"no {path} yet — is the watcher attached to the draft room?")
    return json.loads(path.read_text())


def main(n: int = 8):
    settings, players, adp = board_mod.load_inputs()
    picks = load_picks()
    me = my_team_id(settings)
    order = settings["draft"]["pick_order"] or [t["id"] for t in settings["teams"]]
    full_order = snake_order(order, settings["roster_size"])

    bd = board_mod.build(settings, players, adp, drafted_ids={p["playerId"] for p in picks})
    my_roster = bd[bd.player_id.isin({p["playerId"] for p in picks if p["teamId"] == me})]

    current = len(picks) + 1
    my_next = next((i for i in range(current - 1, len(full_order)) if full_order[i] == me), None)
    until_me = (my_next - (current - 1)) if my_next is not None else -1
    rec = recommend(bd, settings, my_roster, max(until_me, 0))

    on_clock = full_order[current - 1] if current - 1 <= len(full_order) - 1 else None
    teams = {t["id"]: t for t in settings["teams"]}
    tag = " <-- YOU" if on_clock == me else ""
    print(f"pick #{current} R{(current - 1) // len(order) + 1} | on clock: "
          f"{teams.get(on_clock, {}).get('name', '?')}{tag} | your next: "
          f"#{(my_next + 1) if my_next is not None else '-'} (in {until_me})")
    print("roster:", ", ".join(f"{r['name']} ({r['pos']})" for r in my_roster.to_dict("records")) or "empty")
    print("needs:", rec["needs"])

    print("\nTOP AVAILABLE")
    for r in rec["top"][:n]:
        gone = "  <-- likely gone by your pick" if r["likely_gone"] else ""
        inj = f" [{r['injury']}]" if isinstance(r.get("injury"), str) and r["injury"] != "ACTIVE" else ""
        bye = f" bye{r['bye']:.0f}" if pd.notna(r.get("bye")) else ""
        risk = f" (-{r['bye_risk']} bye clash)" if r.get("bye_risk") else ""
        print(f"  {r['name']:<22} {r['pos']:<4} proj {r['proj']:>5}  vorp {r['vorp']:>6}  "
              f"tier {r['tier']}  adp {r['adp']}{bye}{risk}{inj}{gone}")
    print("\nBEST AT EACH POSITION")
    for pos, rows in rec["by_pos"].items():
        if rows:
            print(f"  {pos:<4} " + " | ".join(f"{r['name']} ({r['vorp']})" for r in rows[:3]))
    print("\nSHOULD SURVIVE TO YOUR NEXT PICK")
    for r in rec["survivors"][:6]:
        print(f"  {r['name']:<22} {r['pos']:<4} vorp {r['vorp']:>6}  adp {r['adp']}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
