"""Replacement level must reflect how many QBs the league actually rosters.

the league's strategy (`uv run python -m ffl.strategy show`): "Have two QBs by the end of round 5, no exceptions." That only happens if the
board values QBs the way a 2QB room does. Bench depth used to be a flat league-wide count of
3 backup QBs — a 1QB assumption — which put replacement at ~QB23 instead of ~QB28 and made
every QB look ~47 points cheaper than he is.
"""
import pandas as pd

from ffl import board as board_mod

from .test_board import EMPTY_ADP, make_players

ONE_QB = {
    "team_count": 10,
    "starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 1},
    "roster_size": 16, "name": "1qb", "season": 2026,
}
TWO_QB = {
    "team_count": 10,
    "starters": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 2},
    "roster_size": 17, "name": "2qb", "season": 2026,
}
SUPERFLEX = {**TWO_QB, "starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "OP": 1}}


def test_two_qb_league_rosters_more_qbs_than_one_qb_league():
    assert board_mod.bench_depth(TWO_QB)["QB"] > board_mod.bench_depth(ONE_QB)["QB"]
    # other positions are unaffected by the QB format
    for pos in ["RB", "WR", "TE"]:
        assert board_mod.bench_depth(TWO_QB)[pos] == board_mod.bench_depth(ONE_QB)[pos]


def test_superflex_counted_like_two_qb():
    assert board_mod.bench_depth(SUPERFLEX)["QB"] == board_mod.bench_depth(TWO_QB)["QB"]


def test_one_qb_league_behaviour_is_unchanged():
    """The old constants were right for 1QB: 10 teams x 0.3 = the previous flat 3."""
    assert board_mod.bench_depth(ONE_QB)["QB"] == 3.0
    assert board_mod.bench_depth(ONE_QB)["RB"] == 8.0
    assert board_mod.bench_depth(ONE_QB)["WR"] == 8.0


def test_qb_vorp_is_higher_in_a_2qb_league():
    """Same player pool, deeper QB demand -> lower replacement -> QBs worth more."""
    players = make_players()
    one = board_mod.build(ONE_QB, players, EMPTY_ADP)
    two = board_mod.build(TWO_QB, players, EMPTY_ADP)
    qb1_one = one[one.pos == "QB"].nlargest(1, "proj").iloc[0]
    qb1_two = two[two.pos == "QB"].nlargest(1, "proj").iloc[0]
    assert qb1_two.vorp > qb1_one.vorp, (
        f"QB1 worth {qb1_two.vorp} in 2QB vs {qb1_one.vorp} in 1QB — 2QB must be higher")
