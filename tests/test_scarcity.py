"""An open starting slot whose pool is about to empty gets priced, not ignored.

Practice draft 7 (slot 10, 18 picks between turns): a WR run cut a startable Mike Evans to
3.0 VORP, so a 6th RB at 37.6 won the pick on 0.15 x 37.6 > 1.35 x 3.0. Two more WR runs
followed and the slot was filled 36 projected points worse nine rounds later.
"""
import pandas as pd

from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_recommend_qb import TWO_QB, deep_pool

# ADP mirrors board rank closely enough for the survival test; the fixture sets it explicitly.
ADP_COLS = ["key", "pos", "team", "adp", "adp_rank", "adp_pos_rank", "adp_1qb",
            "adp_1qb_rank", "bye"]


def adp_for(players: pd.DataFrame, wr_adp: float, other_adp: float) -> pd.DataFrame:
    """Give every WR one ADP and everyone else another, so 'will he last?' is controllable."""
    from ffl.board import norm_name
    rows = [{"key": norm_name(r["name"]), "pos": r.pos, "team": r.team,
             "adp": wr_adp if r.pos == "WR" else other_adp, "adp_rank": 1, "adp_pos_rank": 1,
             "adp_1qb": None, "adp_1qb_rank": None, "bye": None}
            for _, r in players.iterrows()]
    return pd.DataFrame(rows, columns=ADP_COLS)


def build(wr_adp: float, other_adp: float, mine_by_pos: dict[str, int]):
    players = deep_pool()
    mine = set()
    for pos, n in mine_by_pos.items():
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    # Drain the WR pool until only a couple sit above replacement — the state a run leaves,
    # and the one that matters: Evans was still startable, at 3.0 VORP.
    wr = players[players.pos == "WR"].sort_values("proj", ascending=False)
    drained = {int(p) for p in wr.head(88).player_id}
    bd = board_mod.build(TWO_QB, players, adp_for(players, wr_adp, other_adp),
                         drafted_ids=mine | drained)
    return bd, bd[bd.player_id.isin(mine)]


ROSTER = {"QB": 2, "RB": 5, "TE": 1}  # WR2 still open, as in draft 7


def test_a_slot_whose_pool_is_about_to_empty_is_filled_now():
    """Every usable WR goes before our next turn: take one even though his VORP is tiny.

    The lift is the cost of waiting — the best WR now minus the best ADP says will survive —
    added to the position, so alternatives stay on the board instead of being filtered out.
    """
    bd, mine = build(wr_adp=5.0, other_adp=900.0, mine_by_pos=ROSTER)
    rec = dw.recommend(bd, TWO_QB, mine, picks_until_me=18, my_picks_left=9)
    assert rec["needs"]["WR"] > 0, "fixture is wrong"
    assert rec["top"][0]["pos"] == "WR", (
        f"an unstartable player still beat the last startable WR: {rec['top'][0]}")
    assert rec["top"][0]["urgency"] > 0, "the lift was not priced as a cost of waiting"
    assert rec["by_pos"]["RB"], (
        "other positions were filtered off the board rather than out-scored")


def test_no_urgency_when_the_pool_will_survive():
    """Same thin pool, but those WRs are going undrafted — no need to reach."""
    bd, mine = build(wr_adp=900.0, other_adp=900.0, mine_by_pos=ROSTER)
    rec = dw.recommend(bd, TWO_QB, mine, picks_until_me=18, my_picks_left=9)
    assert all(r["urgency"] == 0 for r in rec["top"]), "priced urgency on a pool that will last"


def test_no_urgency_when_we_pick_again_immediately():
    """At the turn, nothing can be taken in between."""
    bd, mine = build(wr_adp=5.0, other_adp=900.0, mine_by_pos=ROSTER)
    rec = dw.recommend(bd, TWO_QB, mine, picks_until_me=0, my_picks_left=9)
    assert all(r["urgency"] == 0 for r in rec["top"]), "priced urgency with no picks in between"
