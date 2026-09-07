"""Which ADP feed is "the market" depends on the room, not the league we usually play in.

Public ESPN mock drafts are all one-QB Standard rooms; our league is 2QB. Reading 2QB ADP in a
1QB room mispriced every quarterback and, through `survival()`, every probability attached to
one. The default must stay 2QB — that is our real league — and the override must carry the 1QB
feed's own spread, which the merged adp.csv does not.
"""
import pandas as pd
import pytest

from ffl import board as board_mod


@pytest.fixture
def feeds(tmp_path, monkeypatch):
    two = pd.DataFrame({"name": ["Q1", "R1"], "key": ["q1", "r1"], "pos": ["QB", "RB"],
                        "team": ["BUF", "DET"], "bye": [7, 6], "adp": [2.0, 1.0],
                        "adp_stdev": [1.5, 0.5], "adp_rank": [2, 1], "adp_pos_rank": [1, 1],
                        "adp_1qb": [40.0, 1.0], "adp_1qb_rank": [40, 1], "adp_1qb_pos_rank": [1, 1]})
    one = pd.DataFrame({"name": ["Q1", "R1"], "key": ["q1", "r1"], "pos": ["QB", "RB"],
                        "team": ["BUF", "DET"], "bye": [7, 6], "adp": [40.0, 1.0],
                        "adp_stdev": [9.0, 0.4], "adp_rank": [40, 1], "adp_pos_rank": [1, 1]})
    two.to_csv(tmp_path / "adp.csv", index=False)
    one.to_csv(tmp_path / "adp_1qb.csv", index=False)
    monkeypatch.setattr(board_mod, "DATA", tmp_path)
    return two, one


def test_default_is_the_2qb_feed(feeds, monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    assert board_mod.adp_mode() == "2qb"
    assert board_mod.load_adp().set_index("key").adp["q1"] == 2.0


def test_1qb_mode_uses_the_1qb_mean(feeds, monkeypatch):
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    assert board_mod.load_adp().set_index("key").adp["q1"] == 40.0


def test_1qb_mode_carries_the_1qb_spread_not_the_2qb_one(feeds, monkeypatch):
    """The merged adp.csv keeps the 2QB stdev, which would make QB survival wrong in a 1QB room."""
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    assert board_mod.load_adp().set_index("key").adp_stdev["q1"] == 9.0


def test_1qb_mode_still_provides_the_columns_build_expects(feeds, monkeypatch):
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    got = board_mod.load_adp()
    for col in ("adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank", "bye"):
        assert col in got.columns, col


def test_unknown_mode_falls_back_to_2qb(feeds, monkeypatch):
    monkeypatch.setenv("FFL_ADP_MODE", "superflex")
    assert board_mod.adp_mode() == "2qb"
