"""Tests for the need-weighting in ffl.draft_watch.recommend."""
import pandas as pd

from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import EMPTY_ADP, SETTINGS, make_players


def board_with(drafted):
    players = make_players()
    return players, board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=drafted)


def test_filled_dst_slot_does_not_outrank_real_players():
    """Once D/ST is filled, a second one must not lead the recommendations.

    D/ST projections sit on a different scale from skill players, so a backup defense could
    outscore genuine bench value and head the list.
    """
    players = make_players()
    dst = players[players.pos == "D/ST"].sort_values("proj", ascending=False)
    mine = {int(dst.iloc[0].player_id)}
    bd = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=mine)
    my_roster = bd[bd.player_id.isin(mine)]

    rec = dw.recommend(bd, SETTINGS, my_roster, picks_until_me=1)
    assert rec["needs"]["D/ST"] == 0
    assert rec["top"][0]["pos"] != "D/ST", "a backup D/ST is leading the board"


def test_open_slot_outranks_bench_at_equal_value():
    """A player filling an open starting slot should beat equal value at a filled one."""
    players = make_players()
    te = players[players.pos == "TE"].sort_values("proj", ascending=False)
    mine = {int(te.iloc[0].player_id)}  # TE slot filled
    bd = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=mine)
    my_roster = bd[bd.player_id.isin(mine)]

    rec = dw.recommend(bd, SETTINGS, my_roster, picks_until_me=1)
    top_positions = [r["pos"] for r in rec["top"][:3]]
    assert "TE" not in top_positions or top_positions[0] != "TE"
