"""Pick math, the draft order as the room reveals it, and what the teams in between still need."""
import pytest

from ffl import draft_watch as dw

from .test_recommend_qb import TWO_QB

ORDER = list(range(1, 11))
FULL = dw.snake_order(ORDER, 17)
ME = 7


def test_turn_math_before_my_first_pick():
    t = dw.turn_math(FULL, ME, current=1)
    assert (t["decide"], t["following"], t["horizon"]) == (7, 14, 6)
    assert t["picks_until_me"] == 6 and t["my_picks_left"] == 17
    assert t["window"] == [8, 9, 10, 10, 9, 8]


def test_turn_math_on_the_clock_uses_the_gap_to_my_following_pick():
    t = dw.turn_math(FULL, ME, current=7)
    assert t["picks_until_me"] == 0
    assert (t["decide"], t["following"], t["horizon"]) == (7, 14, 6)
    t = dw.turn_math(FULL, ME, current=14)
    assert (t["decide"], t["following"], t["horizon"]) == (14, 27, 12)


def test_turn_math_at_the_end():
    t = dw.turn_math(FULL, ME, current=167)  # my last pick: round 17 runs 161-170, slot 7 -> 167
    assert t["decide"] == 167 and t["following"] is None and t["horizon"] == 0
    assert t["my_picks_left"] == 1
    t = dw.turn_math(FULL, ME, current=168)
    assert t["decide"] is None and t["picks_until_me"] == -1 and t["my_picks_left"] == 0


def test_slot_one_and_ten_have_back_to_back_picks():
    t = dw.turn_math(FULL, 1, current=1)
    assert (t["decide"], t["following"], t["horizon"]) == (1, 20, 18)
    t = dw.turn_math(FULL, 10, current=10)
    assert (t["decide"], t["following"], t["horizon"]) == (10, 11, 0)


def test_learn_order_from_round_one_picks():
    picks = [{"teamId": 4, "overallPickNumber": 1}, {"teamId": 9, "overallPickNumber": 2}]
    assert dw.learn_order(ORDER, picks) == [4, 9, 1, 2, 3, 5, 6, 7, 8, 10]


def test_learn_order_uses_the_team_on_the_clock():
    picks = [{"teamId": 4, "overallPickNumber": 1}]
    assert dw.learn_order(ORDER, picks, on_clock=7, current=2) == [4, 7, 1, 2, 3, 5, 6, 8, 9, 10]


def test_learn_order_ignores_backfilled_and_later_rounds():
    picks = [{"teamId": 0, "overallPickNumber": 1}] + \
            [{"teamId": t, "overallPickNumber": i + 2} for i, t in enumerate(range(1, 11))]
    learned = dw.learn_order(ORDER, picks)
    assert learned[1:10] == list(range(1, 10)) and learned[0] == 10
    assert dw.learn_order(ORDER, []) == ORDER


def test_pick_order_env_override(monkeypatch):
    settings = {"teams": [{"id": i} for i in range(1, 11)],
                "draft": {"pick_order": ORDER, "order_type": "DRAFT_START"}}
    monkeypatch.delenv("FFL_PICK_ORDER", raising=False)
    assert dw.order_is_placeholder(settings)
    monkeypatch.setenv("FFL_PICK_ORDER", "3,7,1,2,4,5,6,8,9,10")
    assert dw.pick_order(settings) == [3, 7, 1, 2, 4, 5, 6, 8, 9, 10]
    assert not dw.order_is_placeholder(settings)
    monkeypatch.setenv("FFL_PICK_ORDER", "3,7")
    with pytest.raises(SystemExit):
        dw.pick_order(settings)


def test_opponent_demand_counts_picks_whose_team_can_still_start_the_position():
    full = {"QB": 2, "RB": 4, "WR": 2, "TE": 1, "K": 1, "D/ST": 1}  # every slot and both flexes used
    empty = {}
    one_qb = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "D/ST": 1}
    d = dw.opponent_demand(TWO_QB, [full, empty, one_qb, one_qb])
    assert d["QB"] == 3
    assert d["RB"] == 3 and d["WR"] == 3 and d["TE"] == 3  # the open flex keeps RB/WR/TE live for one_qb
    assert d["K"] == 1 and d["D/ST"] == 1
    assert dw.opponent_demand(TWO_QB, [full, full]) == {p: 0 for p in dw.POSITIONS}
