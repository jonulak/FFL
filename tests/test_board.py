"""Regression tests for replacement level / VORP in ffl.board."""
import pandas as pd
import pytest

from ffl import board as board_mod

SETTINGS = {
    "team_count": 10,
    "starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 1},
    "roster_size": 16,
    "name": "test",
    "season": 2026,
}


# roughly the real shape of each position: kickers and defenses live on a much flatter,
# lower scale than skill players, which is what makes their replacement level cheap
SCALE = {"QB": (360.0, 4.0), "RB": (350.0, 4.0), "WR": (340.0, 4.0),
         "TE": (250.0, 3.0), "D/ST": (120.0, 1.0), "K": (150.0, 0.8)}


def make_players(n_per_pos: int = 60) -> pd.DataFrame:
    rows = []
    pid = 1
    for pos, (top, step) in SCALE.items():
        for i in range(n_per_pos):
            rows.append({"player_id": pid, "name": f"{pos}{i}", "pos": pos, "team": f"T{i}",
                         "proj": top - i * step, "injury": "ACTIVE"})
            pid += 1
    return pd.DataFrame(rows)


EMPTY_ADP = pd.DataFrame(columns=["key", "pos", "team", "adp", "adp_rank", "adp_pos_rank",
                                  "adp_1qb", "adp_1qb_rank", "bye"])


def test_top_available_stays_above_replacement_midraft():
    """The best available player at a position must keep positive VORP after a run on it.

    Regression: replacement level dropped BENCH_DEPTH mid-draft, so once a position ran hot
    the replacement index hit 0 — replacement became the best available player, zeroing out
    every VORP at that position and erasing it from recommendations.
    """
    players = make_players()
    wrs = players[players.pos == "WR"].sort_values("proj", ascending=False)
    drafted = set(wrs.head(24).player_id)  # a heavy run on WR

    bd = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=drafted)
    best_wr = bd[(bd.pos == "WR") & (~bd.drafted)].nlargest(1, "proj").iloc[0]
    assert best_wr.vorp > 0, f"best available WR has VORP {best_wr.vorp}; replacement level collapsed"


@pytest.mark.parametrize("pos", ["QB", "RB", "WR", "TE"])
def test_replacement_falls_as_a_position_is_drafted(pos):
    """Taking players off the board should raise, never lower, what is left worth."""
    players = make_players()
    sub = players[players.pos == pos].sort_values("proj", ascending=False)
    drafted = set(sub.head(15).player_id)

    pre = board_mod.build(SETTINGS, players, EMPTY_ADP)
    post = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=drafted)
    assert post[post.pos == pos].repl.iloc[0] <= pre[pre.pos == pos].repl.iloc[0]


def test_predraft_replacement_uses_starters_plus_bench():
    players = make_players()
    bd = board_mod.build(SETTINGS, players, EMPTY_ADP)
    counts = board_mod.starter_counts(SETTINGS)
    import math
    for pos in ["RB", "WR", "TE"]:
        want = math.ceil(counts[pos] + board_mod.BENCH_DEPTH[pos]) - 1
        pool = players[players.pos == pos].sort_values("proj", ascending=False).proj.values
        assert bd[bd.pos == pos].repl.iloc[0] == pytest.approx(pool[want])
