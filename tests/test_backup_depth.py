"""Backups have diminishing returns: the first covers a bye, the fourth covers nothing.

Replaying practice draft 4 through the recommender produced a roster with six QBs — once
every starting slot was full, all remaining players shared one flat discount, so the ranking
fell back to raw VORP and QB (replacement ~QB28 in a 2QB league) won every leftover pick.
"""
from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import EMPTY_ADP
from .test_recommend_qb import TWO_QB, deep_pool

# TWO_QB: QB 2, RB 2, WR 2, TE 1 starters, plus 2 RB/WR/TE flex


def test_backups_held_counts_only_players_who_cannot_start():
    assert dw.backups_held(TWO_QB, {"QB": 2}, "QB") == 0, "the two starters are not backups"
    assert dw.backups_held(TWO_QB, {"QB": 3}, "QB") == 1
    assert dw.backups_held(TWO_QB, {"QB": 5}, "QB") == 3
    # RB may park two extras in the flex before any of them counts as a backup
    assert dw.backups_held(TWO_QB, {"RB": 4}, "RB") == 0
    assert dw.backups_held(TWO_QB, {"RB": 6}, "RB") == 2


def rec_with(mine_by_pos: dict[str, int]):
    players = deep_pool()
    mine = set()
    for pos, n in mine_by_pos.items():
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    return dw.recommend(bd, TWO_QB, bd[bd.player_id.isin(mine)], picks_until_me=1)


def qb_score(n_qbs: int) -> float:
    return next(r["score"] for r in rec_with({"QB": n_qbs, "RB": 4, "WR": 2, "TE": 1})["by_pos"]["QB"])


def test_each_extra_backup_is_worth_less_than_the_last():
    """Roster is otherwise full, so every QB on offer is bench-only."""
    third, fourth, fifth = qb_score(2), qb_score(3), qb_score(4)
    assert third > fourth > fifth, f"backup QBs did not decay: {third}, {fourth}, {fifth}"
    assert fifth > 0, "backups zeroed out entirely"


def test_a_full_bench_at_one_position_lets_another_lead():
    """With four QBs already stashed, a fifth must not head a board of upside picks."""
    rec = rec_with({"QB": 4, "RB": 4, "WR": 2, "TE": 1})
    assert rec["top"][0]["pos"] != "QB", f"a fifth QB still leads: {rec['top'][0]}"
