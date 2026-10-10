from ffl import decisions as D
from ffl import season

CARDS = [{"player_id": 1, "name": "A", "pos": "WR", "call": "START", "gap": 2.5},
         {"player_id": 2, "name": "B", "pos": "WR", "call": "BENCH", "gap": -0.1},
         {"player_id": 3, "name": "C", "pos": "RB", "call": "OUT", "gap": None},
         {"player_id": 4, "name": "D", "pos": "WR", "call": "IR", "gap": None}]


def test_every_kind_has_presets_ending_in_other():
    assert set(D.PRESETS) == set(D.KINDS)
    assert all(v[-1][0] == "other" for v in D.PRESETS.values())
    assert "followed_call" in D.preset_keys("lineup") and "followed_call" not in D.preset_keys("claims")


def test_bench_slots_match_season():
    assert D._BENCH == season.BENCH_SLOTS


def test_followed_lineup():
    assert D.followed_lineup([(1, "WR", "RB/WR/TE"), (2, "RB/WR/TE", "WR")], CARDS) is False, "a BENCH call into a start"
    assert D.followed_lineup([(1, "BE", "WR"), (2, "WR", "BE")], CARDS) is True
    assert D.followed_lineup([(1, "WR", "RB/WR/TE")], CARDS) is True, "a START shuffled between starting slots"
    assert D.followed_lineup([(3, "BE", "IR"), (4, "IR", "BE")], CARDS) is True, "OUT to IR, IR activation to the bench"
    assert D.followed_lineup([(1, "WR", "BE")], CARDS) is False, "benching a START"


def test_followed_sit():
    assert D.followed_sit({"call": "START", "model_call": "START"}, True) is False
    assert D.followed_sit({"call": "BENCH", "model_call": "BENCH"}, True) is True
    assert D.followed_sit({"call": "OUT"}, True) is True
    assert D.followed_sit({"call": "SIT", "model_call": "START"}, False) is True, "standing him back up is the call"
