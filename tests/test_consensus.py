"""consensus.combine: FFA's robust average (Hodges-Lehmann) and the source spread."""
from ffl.consensus import combine


def test_empty_is_nothing():
    assert combine({}) == (None, None, 0)


def test_one_source_is_itself_with_no_spread():
    assert combine({"espn": 12.34}) == (12.3, None, 1)


def test_two_sources_are_todays_mean_and_gap():
    assert combine({"espn": 21.4, "sleeper": 23.6}) == (22.5, 2.2, 2)


def test_three_sources_hodges_lehmann():
    # Walsh averages of 10, 12, 20: 10, 12, 20, 11, 15, 16 -> median (12 + 15) / 2
    assert combine({"a": 10, "b": 12, "c": 20}) == (13.5, 10.0, 3)


def test_five_sources_shrug_off_one_wild_number_and_trim_the_spread():
    proj, spread, n = combine({"a": 15, "b": 16, "c": 15.5, "d": 16.5, "e": 40})
    assert n == 5
    # 15 Walsh averages; the 8th of them sorted is 16.0
    assert proj == 16.0, "the mean would be 20.6"
    assert spread == 1.0, "range after dropping the highest and lowest: 16.5 - 15.5"


def test_equal_weights_reproduce_the_unweighted_answer():
    vals = {"a": 10, "b": 12, "c": 20}
    assert combine(vals, {"a": 1, "b": 1, "c": 1}) == combine(vals)


def test_weights_pull_toward_the_heavier_source():
    vals = {"a": 10, "b": 12, "c": 20}
    assert combine(vals, {"a": 0.1, "b": 0.1, "c": 0.8})[0] > combine(vals)[0]


def test_a_source_missing_from_the_weights_gets_the_mean_weight():
    vals = {"a": 10, "b": 12, "c": 20}
    assert combine(vals, {"a": 1, "b": 1}) == combine(vals)
