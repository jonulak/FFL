"""Which ADP feed is "the market" depends on the room's lineup, and FFL_ADP_MODE can override it.

Public ESPN mock drafts are all one-QB Standard rooms; our league is 2QB. Reading 2QB ADP in a
1QB room mispriced every quarterback and, through `survival()`, every probability attached to
one. A league that starts two QBs (or an OP slot) reads the 2QB feed, any other the 1QB one, and
the 1QB read must carry the 1QB feed's own spread, which the 2QB merge does not.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from ffl import adp, board as board_mod, db, formats, leagues

FX = Path(__file__).parent / "fixtures" / "home_settings.json"   # 10 teams, QB 2, 1 PPR, 2026


def settings_with(**starters) -> dict:
    return {"starters": starters, "team_count": 10, "season": 2026,
            "scoring": [{"stat_id": 53, "abbr": "REC", "label": "Each reception", "points": 1.0}]}


TWO_QB = settings_with(QB=2, RB=2, WR=2)


@pytest.fixture
def feeds():
    """The home league's settings and its two feeds, stored as adp.store stores them."""
    formats.store(leagues.home(), json.loads(FX.read_text()))
    two = pd.DataFrame({"name": ["Q1", "R1"], "key": ["q1", "r1"], "pos": ["QB", "RB"],
                        "team": ["BUF", "DET"], "bye": [7, 6], "adp": [2.0, 1.0],
                        "adp_stdev": [1.5, 0.5], "adp_rank": [2, 1], "adp_pos_rank": [1, 1]})
    one = pd.DataFrame({"name": ["Q1", "R1"], "key": ["q1", "r1"], "pos": ["QB", "RB"],
                        "team": ["BUF", "DET"], "bye": [7, 6], "adp": [40.0, 1.0],
                        "adp_stdev": [9.0, 0.4], "adp_rank": [40, 1], "adp_pos_rank": [1, 1]})
    db.put_adp_feed("2qb", 10, 2026, adp.records(two), {})
    db.put_adp_feed("ppr", 10, 2026, adp.records(one), {})
    return TWO_QB


def test_adp_mode_follows_the_lineup(monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    assert board_mod.adp_mode(settings_with(QB=2, RB=2)) == "2qb"
    assert board_mod.adp_mode(settings_with(QB=1, OP=1, RB=2)) == "2qb"
    assert board_mod.adp_mode(settings_with(QB=1, RB=2, WR=2, TE=1)) == "1qb"
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    assert board_mod.adp_mode(settings_with(QB=2, RB=2)) == "1qb"


def test_default_for_a_2qb_league_is_the_2qb_feed(feeds, monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    got = board_mod.load_adp(feeds).set_index("key")
    assert got.adp["q1"] == 2.0 and got.adp_1qb["q1"] == 40.0


def test_1qb_mode_uses_the_1qb_mean(feeds, monkeypatch):
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    assert board_mod.load_adp(feeds).set_index("key").adp["q1"] == 40.0


def test_a_1qb_league_reads_the_1qb_feed_without_the_override(feeds, monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    assert board_mod.load_adp(settings_with(QB=1, RB=2)).set_index("key").adp["q1"] == 40.0


def test_1qb_mode_carries_the_1qb_spread_not_the_2qb_one(feeds, monkeypatch):
    """The 2QB merge keeps the 2QB stdev, which would make QB survival wrong in a 1QB room."""
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    assert board_mod.load_adp(feeds).set_index("key").adp_stdev["q1"] == 9.0


def test_1qb_mode_still_provides_the_columns_build_expects(feeds, monkeypatch):
    monkeypatch.setenv("FFL_ADP_MODE", "1qb")
    got = board_mod.load_adp(feeds)
    for col in ("adp", "adp_rank", "adp_pos_rank", "adp_1qb", "adp_1qb_rank", "bye"):
        assert col in got.columns, col


def test_unknown_mode_falls_back_to_the_lineup(monkeypatch):
    monkeypatch.setenv("FFL_ADP_MODE", "superflex")
    assert board_mod.adp_mode(TWO_QB) == "2qb"
    assert board_mod.adp_mode(settings_with(QB=1)) == "1qb"


def test_the_1qb_feed_follows_the_league_ppr():
    half = {**TWO_QB, "scoring": [{"stat_id": 53, "abbr": "REC", "label": "", "points": 0.5}]}
    assert adp.feed_keys(TWO_QB) == {"2qb": ("2qb", 10, 2026), "1qb": ("ppr", 10, 2026)}
    assert adp.feed_keys(half)["1qb"] == ("half-ppr", 10, 2026)


def test_a_missing_feed_says_how_to_fetch_it(monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    with pytest.raises(SystemExit, match="ffl.adp"):
        board_mod.load_adp(TWO_QB)


def test_store_fetches_both_feeds_for_the_league(monkeypatch):
    formats.store(leagues.home(), json.loads(FX.read_text()))
    asked = []

    def fake(fmt, teams, year):
        asked.append((fmt, teams, year))
        df = pd.DataFrame({"name": ["Q1"], "key": ["q1"], "pos": ["QB"], "team": ["BUF"], "bye": [7],
                           "adp": [2.0], "adp_rank": [1], "adp_pos_rank": [1]})
        df.attrs["meta"] = {"total_drafts": 12}
        return df

    monkeypatch.setattr(adp, "fetch", fake)
    adp.store(leagues.home())
    assert asked == [("2qb", 10, 2026), ("ppr", 10, 2026)]
    assert db.adp_feed("ppr", 10, 2026)["meta"] == {"total_drafts": 12}
