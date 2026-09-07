"""K/D/ST wait for the run-in, and a second backup at a non-flex position is dead weight."""
from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import EMPTY_ADP
from .test_recommend_qb import TWO_QB, deep_pool


def rec(mine_by_pos: dict[str, int], my_picks_left):
    players = deep_pool()
    mine = set()
    for pos, n in mine_by_pos.items():
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    return dw.recommend(bd, TWO_QB, bd[bd.player_id.isin(mine)], picks_until_me=10,
                        my_picks_left=my_picks_left)


# starters filled except K and D/ST, which is where draft 6 reached for them in rounds 12-13
STARTERS_SET = {"QB": 2, "RB": 4, "WR": 2, "TE": 1}


def test_kicker_and_defense_are_off_the_board_until_the_run_in():
    """0.2 was a multiplier, and by round 12 everything else scores 0.0, so they still won."""
    early = rec(STARTERS_SET, my_picks_left=6)
    assert all(r["pos"] not in ("K", "D/ST") for r in early["top"]), (
        f"a kicker or defense is on the board with 6 picks left: {early['top'][0]}")


def test_they_come_back_when_only_those_slots_are_left():
    """Two picks, two empty slots: now they are the only thing to take."""
    late = rec(STARTERS_SET, my_picks_left=2)
    assert {r["pos"] for r in late["top"]} <= {"K", "D/ST"}, late["top"][:3]


def test_a_fourth_qb_is_dead_weight_in_a_two_qb_league():
    """QB3 is insurance; QB4 can only ever be third string, so he must not lead a bench pick."""
    with_qb3 = rec({**STARTERS_SET, "QB": 3}, my_picks_left=4)
    assert with_qb3["top"][0]["pos"] != "QB", f"a QB4 leads: {with_qb3['top'][0]}"
    assert not any(r["score"] > -900 for r in with_qb3["by_pos"]["QB"]), (
        "QB4 is still priced as a real option")


def test_the_third_qb_himself_is_still_available():
    """The rule must not blacklist the backup we actually want."""
    r = rec(STARTERS_SET, my_picks_left=4)
    qbs = [q for q in r["by_pos"]["QB"] if q["score"] > -900]
    assert qbs, "QB3 was blacklisted along with the QB4s"


def test_a_third_tight_end_is_dead_weight():
    """TE is flex-eligible so the QB4 rule never caught it; a full replay ended with seven TEs."""
    two = rec({**STARTERS_SET, "TE": 2}, my_picks_left=6)
    assert not any(t["score"] > -900 for t in two["by_pos"]["TE"]), "a TE3 is still priced"
    one = rec(STARTERS_SET, my_picks_left=6)
    assert any(t["score"] > -900 for t in one["by_pos"]["TE"]), "a TE2 must stay available"
