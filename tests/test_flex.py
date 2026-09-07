"""Flex capacity has to shrink as the roster fills it.

Practice draft 3: the board took five RBs and no WR through six rounds, and at pick 74 a sixth
RB still carried flex weighting while two WR starting slots sat open.
"""
from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import EMPTY_ADP, SETTINGS, make_players
from .test_recommend_qb import TWO_QB, deep_pool


def test_flex_left_shrinks_as_extra_players_spill_into_it():
    # 2 flex; starters are RB 2 / WR 2 / TE 1
    assert dw.flex_left(TWO_QB, {}) == 2
    assert dw.flex_left(TWO_QB, {"RB": 2, "WR": 2}) == 2, "starters must not consume flex"
    assert dw.flex_left(TWO_QB, {"RB": 3}) == 1
    assert dw.flex_left(TWO_QB, {"RB": 4}) == 0
    assert dw.flex_left(TWO_QB, {"RB": 6}) == 0, "must not go negative"
    assert dw.flex_left(TWO_QB, {"RB": 3, "WR": 3}) == 0
    assert dw.flex_left(TWO_QB, {"QB": 4}) == 2, "QB does not spill into an RB/WR/TE flex"


def test_extra_rb_does_not_outrank_an_open_wr_slot_once_flex_is_full():
    players = deep_pool()
    mine = set()
    # RB2 + both flex spots filled with RBs, and not a single WR yet
    for pos, n in [("RB", 4), ("QB", 2)]:
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    my_roster = bd[bd.player_id.isin(mine)]

    rec = dw.recommend(bd, TWO_QB, my_roster, picks_until_me=1)
    assert rec["needs"]["WR"] == 2, "fixture is wrong"
    assert rec["top"][0]["pos"] != "RB", (
        f"a bench RB leads while two WR slots are open: {rec['top'][0]}")


def rb_score(n_rbs: int) -> float:
    """Score of the best available RB with n_rbs already rostered (2 RB slots + 2 flex)."""
    players = deep_pool()
    mine = {int(p) for p in players[players.pos == "RB"]
            .sort_values("proj", ascending=False).head(n_rbs).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    rec = dw.recommend(bd, TWO_QB, bd[bd.player_id.isin(mine)], picks_until_me=1)
    return next(r["score"] for r in rec["by_pos"]["RB"])


def test_an_rb_is_worth_more_while_a_flex_spot_is_still_open():
    """The other side of it: the discount must land only once the flex is actually full, so
    a 4th RB (filling the last flex) still outscores a 5th (pure bench) of similar value."""
    assert dw.flex_left(TWO_QB, {"RB": 3}) == 1 and dw.flex_left(TWO_QB, {"RB": 4}) == 0
    assert rb_score(3) > rb_score(4), "flex-open RB not valued above a bench RB"
