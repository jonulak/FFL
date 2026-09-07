"""Live draft watcher: polls ESPN's draft endpoint, tracks picks, recommends best available.

    uv run python -m ffl.draft_watch            # loop forever (5s poll)
    uv run python -m ffl.draft_watch once       # single snapshot

Outputs: data/draft_state.json (full state), journal/draft/picks.csv (append-only pick log), stdout.
Uses the raw mDraftDetail view because espn-api's League.draft is empty until `drafted` is true.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime

import pandas as pd

from . import board as board_mod
from .config import DATA, JOURNAL, MY_TEAM_ID, SWID, raw_league_get

POLL_S = 5
PICKS_CSV = JOURNAL / "draft" / "picks.csv"
STATE = DATA / "draft_state.json"
NEED_WEIGHT = {"starter_open": 1.35, "flex_open": 1.1, "bench": 0.9}


def my_team_id(settings: dict) -> int:
    if MY_TEAM_ID:
        return MY_TEAM_ID
    if SWID:
        for t in settings["teams"]:
            if SWID in t.get("owner_swids", []):
                return t["id"]
    raise SystemExit("Could not identify my team: set MY_TEAM_ID in .env")


def snake_order(pick_order: list[int], rounds: int) -> list[int]:
    out = []
    for r in range(rounds):
        out += pick_order if r % 2 == 0 else pick_order[::-1]
    return out


def fetch_picks() -> dict:
    d = raw_league_get("mDraftDetail")
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


def recommend(bd: pd.DataFrame, settings: dict, my_roster: pd.DataFrame, picks_until_me: int) -> dict:
    avail = bd[~bd.drafted].copy()
    counts = my_roster.pos.value_counts().to_dict() if len(my_roster) else {}
    left = open_slots(settings, counts)
    flex_slots = sum(n for s, n in settings["starters"].items() if _is_flex(s))
    weights = []
    for _, r in avail.iterrows():
        if left.get(r.pos, 0) > 0:
            w = NEED_WEIGHT["starter_open"]
            if r.pos == "QB":
                w += 0.15  # 2QB league: never leave the draft short at QB
        elif r.pos in ("RB", "WR", "TE", "QB") and flex_slots:
            w = NEED_WEIGHT["flex_open"]
        else:
            w = NEED_WEIGHT["bench"]
        if r.pos in ("K", "D/ST") and len(my_roster) < settings["roster_size"] - 3:
            w = 0.2  # don't reach for K/DST until the end
        weights.append(w)
    avail["score"] = (avail.vorp.clip(lower=0) * weights).round(1)
    avail = avail.sort_values("score", ascending=False)
    # who is likely to survive until my next pick, by ADP
    current = int(bd.drafted.sum()) + 1
    avail["likely_gone"] = avail.adp.fillna(999) < current + picks_until_me - 2
    cols = ["name", "pos", "team", "bye", "proj", "vorp", "tier", "adp", "score", "likely_gone", "injury"]
    return {
        "needs": left,
        "top": avail.head(10)[cols].to_dict("records"),
        "by_pos": {p: avail[avail.pos == p].head(4)[cols].to_dict("records") for p in ["QB", "RB", "WR", "TE"]},
        "survivors": avail[~avail.likely_gone].head(8)[cols].to_dict("records"),
    }


def run(once: bool = False):
    settings, players, adp = board_mod.load_inputs()
    me = my_team_id(settings)
    teams = {t["id"]: t for t in settings["teams"]}
    order = settings["draft"]["pick_order"] or [t["id"] for t in settings["teams"]]
    rounds = settings["roster_size"]
    full_order = snake_order(order, rounds)
    id_to_row = players.set_index("player_id")
    seen = 0
    PICKS_CSV.parent.mkdir(parents=True, exist_ok=True)
    if not PICKS_CSV.exists():
        with PICKS_CSV.open("w", newline="") as f:
            csv.writer(f).writerow(["overall", "round", "pick", "team_id", "team", "player_id", "player", "pos", "nfl", "adp_2qb", "board_rank_at_pick", "proj", "ts"])

    print(f"Watching league {settings['league_id']} — I am team {me} ({teams[me]['name']}), slot {order.index(me)+1 if me in order else '?'} of {len(order)}", flush=True)
    while True:
        try:
            d = fetch_picks()
        except Exception as e:  # network hiccup: keep polling
            print(f"[{datetime.now():%H:%M:%S}] fetch error: {e}", flush=True)
            time.sleep(POLL_S)
            continue
        picks = sorted(d["picks"], key=lambda p: p.get("overallPickNumber", 0))
        drafted_ids = {p["playerId"] for p in picks if p.get("playerId", 0) > 0}
        bd = board_mod.build(settings, players, adp, drafted_ids=drafted_ids)
        pre_board = None
        if len(picks) > seen:
            pre_board = board_mod.build(settings, players, adp, drafted_ids={p["playerId"] for p in picks[:seen]})
            pre_rank = pre_board.set_index("player_id").board_rank
            with PICKS_CSV.open("a", newline="") as f:
                w = csv.writer(f)
                for p in picks[seen:]:
                    pid = p["playerId"]
                    row = id_to_row.loc[pid] if pid in id_to_row.index else None
                    name = row["name"] if row is not None else f"id{pid}"
                    pos = row["pos"] if row is not None else "?"
                    br = pre_rank.get(pid)
                    a = bd.set_index("player_id").adp.get(pid)
                    tag = " <== ME" if p["teamId"] == me else ""
                    print(f"[{datetime.now():%H:%M:%S}] {p['roundId']}.{p['roundPickNumber']:02d} (#{p['overallPickNumber']}) "
                          f"{teams.get(p['teamId'],{}).get('abbrev','?'):>5} -> {name} ({pos}) board#{br if pd.notna(br) else '-'} adp {a if pd.notna(a) else '-'}{tag}", flush=True)
                    w.writerow([p["overallPickNumber"], p["roundId"], p["roundPickNumber"], p["teamId"], teams.get(p["teamId"], {}).get("name"),
                                pid, name, pos, row["team"] if row is not None else "", a, br, row["proj"] if row is not None else "", datetime.now().isoformat(timespec="seconds")])
            seen = len(picks)

        current = len(picks) + 1
        on_clock = full_order[current - 1] if current - 1 < len(full_order) else None
        my_next = next((i for i in range(current - 1, len(full_order)) if full_order[i] == me), None)
        picks_until_me = (my_next - (current - 1)) if my_next is not None else -1
        my_roster = bd[bd.drafted & bd.player_id.isin({p["playerId"] for p in picks if p["teamId"] == me})]
        rec = recommend(bd, settings, my_roster, max(picks_until_me, 0))
        recent = [id_to_row.loc[p["playerId"]]["pos"] for p in picks[-6:] if p["playerId"] in id_to_row.index]
        runs = {pos: recent.count(pos) for pos in set(recent) if recent.count(pos) >= 3}
        state = {
            "ts": datetime.now().isoformat(timespec="seconds"), "in_progress": d["in_progress"], "drafted": d["drafted"],
            "picks_made": len(picks), "current_pick": current, "round": (current - 1) // len(order) + 1,
            "on_clock": teams.get(on_clock, {}).get("name") if on_clock else None, "on_clock_is_me": on_clock == me,
            "picks_until_me": picks_until_me, "my_next_overall": (my_next + 1) if my_next is not None else None,
            "my_roster": my_roster[["name", "pos", "team", "bye", "proj", "tier"]].to_dict("records"),
            "runs": runs, "recommend": rec,
            "rosters": {teams[t]["name"]: [id_to_row.loc[p["playerId"]]["name"] + f" ({id_to_row.loc[p['playerId']]['pos']})" for p in picks if p["teamId"] == t and p["playerId"] in id_to_row.index] for t in teams},
        }
        STATE.write_text(json.dumps(state, indent=1, default=str))
        if pre_board is not None or once or len(picks) == 0 and seen == 0:
            hdr = f"--- pick #{current} R{state['round']} | on clock: {state['on_clock']}{' (ME)' if state['on_clock_is_me'] else ''} | my next in {picks_until_me} | needs {rec['needs']} | runs {runs}"
            print(hdr, flush=True)
            print(pd.DataFrame(rec["top"])[["name", "pos", "proj", "vorp", "tier", "adp", "score", "likely_gone"]].to_string(index=False), flush=True)
        if once:
            break
        if d["drafted"]:
            print("Draft complete.", flush=True)
            break
        time.sleep(POLL_S)


if __name__ == "__main__":
    run(once=len(sys.argv) > 1 and sys.argv[1] == "once")
