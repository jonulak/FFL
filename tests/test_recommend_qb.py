"""A backup QB must not outrank an open starting slot in a 2QB league.

Seen live in the 2026-09-06 practice draft: the recommender took a QB in rounds 6, 7 and 8
with WR2 and TE1 still empty, and by pick 74 the entire top four were QBs.
"""
import pandas as pd

from ffl import board as board_mod
from ffl import draft_watch as dw

from .test_board import EMPTY_ADP, SETTINGS

TWO_QB = {**SETTINGS, "starters": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1,
                                   "RB/WR/TE": 2}}

# Pool *sizes* drive this bug as much as slopes. The real QB pool is only ~32 deep, so a
# replacement index of starters+bench lands near its bottom (replacement ~QB28, 194 pts on
# the real board) and every rosterable QB keeps a large VORP — while the 120-deep WR pool
# puts replacement up at WR30-ish. (top, per-rank drop, pool size)
SPEC = {"QB": (361.0, 5.8, 32), "RB": (348.0, 2.2, 90), "WR": (333.0, 1.6, 120),
        "TE": (247.0, 3.5, 40), "D/ST": (120.0, 1.0, 32), "K": (150.0, 0.8, 32)}


def deep_pool() -> pd.DataFrame:
    rows, pid = [], 1
    for pos, (top, step, n) in SPEC.items():
        for i in range(n):
            rows.append({"player_id": pid, "name": f"{pos}{i}", "pos": pos, "team": f"T{i}",
                         "proj": top - i * step, "injury": "ACTIVE"})
            pid += 1
    return pd.DataFrame(rows)


def rec_with(mine_by_pos: dict[str, int]):
    players = deep_pool()
    mine = set()
    for pos, n in mine_by_pos.items():
        sub = players[players.pos == pos].sort_values("proj", ascending=False)
        mine |= {int(p) for p in sub.head(n).player_id}
    bd = board_mod.build(TWO_QB, players, EMPTY_ADP, drafted_ids=mine)
    return bd, dw.recommend(bd, TWO_QB, bd[bd.player_id.isin(mine)], picks_until_me=1)


def test_third_qb_does_not_outrank_an_open_starting_slot():
    """The flex is RB/WR/TE, so a third QB can only ride the bench: his value is injury/bye
    insurance, not a starter's VORP. The bench weight (0.9) is nowhere near enough."""
    bd, rec = rec_with({"QB": 2, "RB": 2})
    assert rec["needs"]["QB"] == 0 and rec["needs"]["WR"] == 2, "fixture is wrong"
    qb = bd[(bd.pos == "QB") & (~bd.drafted)].vorp.max()
    wr = bd[(bd.pos == "WR") & (~bd.drafted)].vorp.max()
    assert qb > wr, f"fixture does not reproduce the QB VORP inflation (QB {qb} <= WR {wr})"
    assert rec["top"][0]["pos"] != "QB", (
        f"a QB3 leads the board while WR2 is open: {rec['top'][0]}")


def test_backup_qb_keeps_some_value():
    """Discounted, not blacklisted: a 2QB league starts 20 QBs, so the waiver wire is bare
    and a QB3 is still worth a late pick — unlike a second kicker."""
    _, rec = rec_with({"QB": 2})
    qbs = [r for r in rec["by_pos"]["QB"] if r["vorp"] > 0]
    assert qbs, "fixture has no QB with positive VORP left"
    assert qbs[0]["score"] > 0, "a QB3 was zeroed out like a backup kicker"
