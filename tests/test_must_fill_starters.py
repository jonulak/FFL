"""Late in the draft, an empty starting slot outranks everything else.

Practice draft 3 finished with eight RBs, five QBs and *no WR at all*, both WR slots empty,
because need weighting is a flat 1.35 and the VORP gap between the best RB left and the best
WR left is routinely wider than that.
"""
from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import EMPTY_ADP
from .test_recommend_qb import TWO_QB, deep_pool


def rec(mine_by_pos: dict[str, int], my_picks_left: int | None):
    players = deep_pool()
    mine = set()
    for pos, n in mine_by_pos.items():
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        # take from deep in each pool so the leftovers are genuinely poor
        mine |= {int(p) for p in sub.head(n).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    return dw.recommend(bd, TWO_QB, bd[bd.player_id.isin(mine)], picks_until_me=10,
                        my_picks_left=my_picks_left)


ROSTER = {"RB": 8, "QB": 5, "TE": 2, "K": 1, "D/ST": 1}  # what draft 3 actually ended up with


def test_last_picks_go_to_unfilled_starting_slots():
    r = rec(ROSTER, my_picks_left=2)
    assert r["needs"]["WR"] == 2, "fixture is wrong"
    assert all(x["pos"] == "WR" for x in r["top"][:2]), (
        f"with 2 picks left and 2 WR slots empty, the board still offers {r['top'][0]}")


def test_the_guard_does_not_fire_while_there_is_room_to_manoeuvre():
    """Plenty of picks left: players outside the unfilled slots are still on the board.

    Checked through by_pos rather than the top 10: once every full position is discounted,
    WRs lead this fixture either way, and what marks the guard firing is that other positions
    are removed from consideration altogether.
    """
    assert rec(ROSTER, my_picks_left=9)["by_pos"]["QB"], "guard fired far too early"
    assert not rec(ROSTER, my_picks_left=2)["by_pos"]["QB"], "guard did not restrict the board"


def test_no_guard_without_a_pick_budget():
    """Callers that do not pass my_picks_left (tests, ad-hoc use) keep the old behaviour."""
    assert rec(ROSTER, my_picks_left=None)["top"], "recommend broke without a pick budget"
