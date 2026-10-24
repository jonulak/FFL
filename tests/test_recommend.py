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


def test_backup_dst_never_leads_when_skill_players_are_below_replacement():
    """Late-draft case: everything usable is below replacement, so a spare D/ST must not lead.

    Seen live in a mock — with K and D/ST already rostered, three defenses headed the board
    ahead of every usable skill player, because their VORP was the only positive number left.
    Every skill position has to be drained for this to reproduce: one undrained position (a
    QB pool, say) will top the board on its own and mask the bug.
    """
    players = make_players()
    mine, drafted = set(), set()
    for pos, n in [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("D/ST", 1), ("K", 1)]:
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    # drain every skill position past its replacement level so nothing left scores above 0
    for pos in ["QB", "RB", "WR", "TE"]:
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        drafted |= {int(p) for p in sub.head(55).player_id}

    bd = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=mine | drafted)
    my_roster = bd[bd.player_id.isin(mine)]
    rec = dw.recommend(bd, SETTINGS, my_roster, picks_until_me=1)

    assert rec["needs"]["D/ST"] == 0 and rec["needs"]["K"] == 0, "fixture did not fill K/DST"
    skill = [r for r in rec["top"] if r["pos"] in ("QB", "RB", "WR", "TE")]
    assert skill, "no usable player in the top 10 at all — K/DST have taken over the board"
    assert max(r["vorp"] for r in skill) <= 0, (
        "fixture did not drain the skill pool; the bug would be masked")
    assert rec["top"][0]["pos"] not in ("K", "D/ST"), (
        f"backup {rec['top'][0]['pos']} leads the late-round board")


def _te_scores(caps):
    from .test_recommend_qb import TWO_QB, deep_pool
    players = deep_pool()
    mine = set()
    for pos, n in {"QB": 2, "RB": 2, "WR": 2, "TE": 2}.items():
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    rec = dw.recommend(bd, TWO_QB, bd[bd.player_id.isin(mine)], picks_until_me=1, caps=caps)
    return [r["score"] for r in rec["by_pos"]["TE"]]


def test_te_cap_from_league_caps():
    """The TE3 rule is the league's TE cap, not a constant: raise it and a third TE is priced again."""
    from ffl import strategy
    raised = _te_scores({**strategy.caps(), "TE": 3})
    assert raised and all(s > -999 for s in raised), "TE cap 3 with two held: a third TE is still blacklisted"
    assert all(s == -999 for s in _te_scores(None)), "default caps (TE 2): a third TE must be off the board"
