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


def test_urgency_fires_while_we_are_on_the_clock():
    """The pick being decided is the one that needs the price on it.

    `likely_gone` used to be `adp < current + picks_until_me - 2`, so on the clock
    (picks_until_me == 0) nothing ever qualified and urgency was 0.0 at the only moment it had to
    work. What matters is the gap to my pick *after* this one.
    """
    bd, mine = build(wr_adp=5.0, other_adp=900.0, mine_by_pos=ROSTER)
    rec = dw.recommend(bd, TWO_QB, mine, picks_until_me=0, my_picks_left=9, horizon=18)
    assert rec["horizon"] == 18
    assert rec["top"][0]["pos"] == "WR", f"the doomed WR slot was not lifted: {rec['top'][0]}"
    assert rec["top"][0]["urgency"] > 0, "no cost of waiting priced on the clock"


def test_back_to_back_picks_have_no_urgency():
    """At the turn there is genuinely nothing in between, so nothing can be taken."""
    bd, mine = build(wr_adp=5.0, other_adp=900.0, mine_by_pos=ROSTER)
    rec = dw.recommend(bd, TWO_QB, mine, picks_until_me=0, my_picks_left=9, horizon=0)
    assert all(r["urgency"] == 0 for r in rec["top"]), "priced urgency with no picks in between"
    assert all(r["survive_prob"] == 1.0 and not r["likely_gone"] for r in rec["top"])


def test_heads_up_and_on_clock_price_the_same_window():
    """Advice given two picks early must not change when the clock reaches us: both tables are
    about the same pick of ours, so they have to agree on who survives it."""
    bd, mine = build(wr_adp=5.0, other_adp=900.0, mine_by_pos=ROSTER)
    early = dw.recommend(bd, TWO_QB, mine, picks_until_me=2, my_picks_left=9, horizon=18)
    now = dw.recommend(bd, TWO_QB, mine, picks_until_me=0, my_picks_left=9, horizon=18)
    assert [r["name"] for r in early["top"]] == [r["name"] for r in now["top"]]
    assert [r["urgency"] for r in early["top"]] == [r["urgency"] for r in now["top"]]


def test_a_run_at_a_capped_position_is_limited_by_what_the_teams_ahead_need():
    """ADP says every TE goes; no team picking before us can still start one."""
    bd, mine = build(wr_adp=900.0, other_adp=5.0, mine_by_pos={"QB": 2, "RB": 5, "WR": 2})
    assert dw.open_slots(TWO_QB, {"QB": 2, "RB": 5, "WR": 2})["TE"] == 1

    def te_urgency(demand):
        rec = dw.recommend(bd, TWO_QB, mine, picks_until_me=0, my_picks_left=9, horizon=18,
                           opp_demand=demand)
        return rec["by_pos"]["TE"][0]["urgency"]

    uncapped, capped = te_urgency(None), te_urgency({"TE": 0})
    assert uncapped > capped > 0, f"cap should shrink the run, not erase it: {uncapped} -> {capped}"


def test_survival_uses_the_spread_not_just_the_mean():
    """A tight ADP is a near-certainty; a wide one is a coin flip at the same mean."""
    tight, wide = dw.survival([30.0, 30.0], [2.0, 12.0], at_pick=40)
    assert tight < 0.05
    assert 0.15 < wide < 0.5
    assert dw.survival([30.0], [3.0], at_pick=20)[0] > 0.99
    assert dw.survival([float("nan")], [float("nan")], at_pick=40)[0] == 1.0
    assert dw.survival([30.0], None, at_pick=40)[0] < 0.2  # no stdev column at all
