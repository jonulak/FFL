"""Live draft watcher: polls ESPN's draft endpoint, tracks picks, recommends best available.

    uv run python -m ffl.draft_watch            # live: read picks off the draft room socket
    uv run python -m ffl.draft_watch once       # single REST snapshot (pre-draft sanity check)
    uv run python -m ffl.draft_watch rest       # REST polling loop (post-draft recap only)

Outputs: data/draft_state.json (full state), journal/draft/picks.csv (append-only pick log), stdout.
ESPN's REST mDraftDetail view is useless *during* a live draft — every slot stays at
playerId -1 until the draft finishes — so live mode reads the draft room's websocket
over CDP instead (see ffl/draft_socket.py). REST stays for pre-draft checks; whether it backfills after a draft is unverified.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

from . import board as board_mod
from .config import DATA, JOURNAL, MY_TEAM_ID, SWID, raw_league_get

POLL_S = 5
# a mock/practice run (FFL_DATA_DIR set) must not pollute the real draft log
PICKS_CSV = (DATA / "picks.csv") if os.environ.get("FFL_DATA_DIR") else (JOURNAL / "draft" / "picks.csv")
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


def recommend(bd: pd.DataFrame, settings: dict, my_roster: pd.DataFrame, picks_until_me: int) -> dict:
    avail = bd[~bd.drafted].copy()
    counts = my_roster.pos.value_counts().to_dict() if len(my_roster) else {}
    left = open_slots(settings, counts)
    flex_slots = sum(n for s, n in settings["starters"].items() if _is_flex(s))
    flex_pos = flex_positions(settings)
    weights = []
    for _, r in avail.iterrows():
        if left.get(r.pos, 0) > 0:
            w = NEED_WEIGHT["starter_open"]
            if r.pos == "QB":
                w += 0.15  # 2QB league: never leave the draft short at QB
        elif r.pos in flex_pos and flex_slots:
            w = NEED_WEIGHT["flex_open"]
        else:
            w = NEED_WEIGHT["bench"]
        if r.pos in ("K", "D/ST") and len(my_roster) < settings["roster_size"] - 3:
            w = 0.2  # don't reach for K/DST until the end
        weights.append(w)
    # Weight only the positive part: below replacement, raw VORP is already the honest
    # cross-position ranking, and scaling it would penalise the positions we most need.
    # Clipping instead (the old behaviour) flattened every such player to 0, leaving the
    # tie-break to raw projection — which is not comparable across positions and floated
    # backup QBs and spare defenses above usable starters late in the draft.
    vorp = avail.vorp.to_numpy()
    avail["score"] = np.where(vorp > 0, vorp * weights, vorp).round(1)
    # A K/DST whose slot is filled is unusable however good its VORP looks: you only start one.
    filled = avail.pos.isin(["K", "D/ST"]) & avail.pos.map(lambda p: left.get(p, 0) == 0)
    avail.loc[filled, "score"] = -999.0
    avail = avail.sort_values(["score", "proj"], ascending=False)
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


class Watcher:
    """Board state + logging, driven by whatever pick feed it is handed."""

    def __init__(self):
        self.settings, self.players, self.adp = board_mod.load_inputs()
        self.me = my_team_id(self.settings)
        self.teams = {t["id"]: t for t in self.settings["teams"]}
        self.order = self.settings["draft"]["pick_order"] or [t["id"] for t in self.settings["teams"]]
        self.rounds = self.settings["roster_size"]
        self.full_order = snake_order(self.order, self.rounds)
        self.id_to_row = self.players.set_index("player_id")
        self.seen = 0
        PICKS_CSV.parent.mkdir(parents=True, exist_ok=True)
        if not PICKS_CSV.exists():
            with PICKS_CSV.open("w", newline="") as f:
                csv.writer(f).writerow(["overall", "round", "pick", "team_id", "team", "player_id", "player", "pos", "nfl", "adp_2qb", "board_rank_at_pick", "proj", "ts"])

    def banner(self, source: str):
        slot = self.order.index(self.me) + 1 if self.me in self.order else "?"
        print(f"Watching league {self.settings['league_id']} via {source} — I am team {self.me} "
              f"({self.teams[self.me]['name']}), slot {slot} of {len(self.order)}", flush=True)

    def tick(self, picks: list[dict], on_clock: int | None = None, done: bool = False) -> dict:
        """Ingest the current pick list; log anything new; return (and persist) full state."""
        drafted_ids = {p["playerId"] for p in picks}
        bd = board_mod.build(self.settings, self.players, self.adp, drafted_ids=drafted_ids)
        fresh = len(picks) > self.seen
        if fresh:
            pre = board_mod.build(self.settings, self.players, self.adp,
                                  drafted_ids={p["playerId"] for p in picks[:self.seen]})
            pre_rank = pre.set_index("player_id").board_rank
            adp_by_id = bd.set_index("player_id").adp
            with PICKS_CSV.open("a", newline="") as f:
                w = csv.writer(f)
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
                    w.writerow([p["overallPickNumber"], p["roundId"], p["roundPickNumber"], p["teamId"],
                                self.teams.get(p["teamId"], {}).get("name"), pid, name, pos,
                                row["team"] if row is not None else "", a, br,
                                row["proj"] if row is not None else "", datetime.now().isoformat(timespec="seconds")])
            self.seen = len(picks)

        current = len(picks) + 1
        if on_clock is None:  # no live feed: infer from the snake order
            on_clock = self.full_order[current - 1] if current - 1 < len(self.full_order) else None
        my_next = next((i for i in range(current - 1, len(self.full_order)) if self.full_order[i] == self.me), None)
        picks_until_me = (my_next - (current - 1)) if my_next is not None else -1
        my_roster = bd[bd.drafted & bd.player_id.isin({p["playerId"] for p in picks if p["teamId"] == self.me})]
        rec = recommend(bd, self.settings, my_roster, max(picks_until_me, 0))
        recent = [self.id_to_row.loc[p["playerId"]]["pos"] for p in picks[-6:] if p["playerId"] in self.id_to_row.index]
        runs = {pos: recent.count(pos) for pos in set(recent) if recent.count(pos) >= 3}
        state = {
            "ts": datetime.now().isoformat(timespec="seconds"), "drafted": done,
            "picks_made": len(picks), "current_pick": current, "round": (current - 1) // len(self.order) + 1,
            "on_clock": self.teams.get(on_clock, {}).get("name") if on_clock else None, "on_clock_is_me": on_clock == self.me,
            "picks_until_me": picks_until_me, "my_next_overall": (my_next + 1) if my_next is not None else None,
            "my_roster": my_roster[["name", "pos", "team", "bye", "proj", "tier"]].to_dict("records"),
            "runs": runs, "recommend": rec,
            "rosters": {self.teams[t]["name"]: [self.id_to_row.loc[p["playerId"]]["name"] + f" ({self.id_to_row.loc[p['playerId']]['pos']})"
                                                for p in picks if p["teamId"] == t and p["playerId"] in self.id_to_row.index] for t in self.teams},
        }
        STATE.write_text(json.dumps(state, indent=1, default=str))
        state["_fresh"] = fresh
        state["_rec"] = rec
        return state

    def print_board(self, state: dict):
        rec = state["_rec"]
        print(f"--- pick #{state['current_pick']} R{state['round']} | on clock: {state['on_clock']}"
              f"{' (ME)' if state['on_clock_is_me'] else ''} | my next in {state['picks_until_me']} "
              f"| needs {rec['needs']} | runs {state['runs']}", flush=True)
        print(pd.DataFrame(rec["top"])[["name", "pos", "proj", "vorp", "tier", "adp", "score", "likely_gone"]].to_string(index=False), flush=True)


def numbered(picks: list[dict], n_teams: int) -> list[dict]:
    """Socket picks arrive in draft order; derive round / round-pick numbers."""
    out = []
    for i, p in enumerate(picks):
        out.append({**p, "overallPickNumber": i + 1, "roundId": i // n_teams + 1, "roundPickNumber": i % n_teams + 1})
    return out


def run_socket():
    """Live mode: read picks off the draft room websocket via the open Chrome tab."""
    from .draft_socket import DraftFeed

    w = Watcher()
    n_teams = len(w.order)
    total = n_teams * w.rounds
    with DraftFeed(cache=DATA / "socket_picks.json") as feed:
        print(f"Attached to {feed.url}", flush=True)
        w.banner("draft-room socket")
        if feed.picks:
            print(f"Resuming with {len(feed.picks)} picks from cache", flush=True)
        w.print_board(w.tick(numbered(feed.picks, n_teams), feed.on_clock))
        while True:
            if not feed.pump(1000):  # CDP link dropped (tab reloaded, Chrome restarted)
                print(f"[{datetime.now():%H:%M:%S}] lost the draft tab; reattaching…", flush=True)
                time.sleep(2)
                try:
                    print(f"  reattached to {feed.reconnect()}", flush=True)
                    print("  NOTE: picks made while detached were missed — check the room's "
                          "Pick History against journal/draft/picks.csv", flush=True)
                except Exception as e:
                    print(f"  reattach failed: {e}", flush=True)
                continue
            state = w.tick(numbered(feed.picks, n_teams), feed.on_clock, done=len(feed.picks) >= total)
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
            d = fetch_picks()
        except Exception as e:  # network hiccup: keep polling
            print(f"[{datetime.now():%H:%M:%S}] fetch error: {e}", flush=True)
            time.sleep(POLL_S)
            continue
        picks = sorted(d["picks"], key=lambda p: p.get("overallPickNumber", 0))
        state = w.tick(picks, done=bool(d["drafted"]))
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
