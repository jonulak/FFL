"""The queue file is edited between turns, sometimes seconds before a pick, so its parsing
has to be forgiving of comments and blank lines and exact about which picks a section covers.
"""
from ffl import autodraft
from ffl.autodraft import candidates, parse_queue

QUEUE = """
# round 1
4429795 Jahmyr Gibbs
@37
4689114 Jaxson Dart
4360310 Trevor Lawrence
@90-120
3052587 Baker Mayfield   # QB3 window
"""


def test_unheadered_lines_apply_to_every_pick():
    assert parse_queue(QUEUE, 1) == [(4429795, "Jahmyr Gibbs")]
    assert parse_queue(QUEUE, 200) == [(4429795, "Jahmyr Gibbs")]


def test_single_pick_section():
    assert parse_queue(QUEUE, 37) == [(4429795, "Jahmyr Gibbs"),
                                      (4689114, "Jaxson Dart"),
                                      (4360310, "Trevor Lawrence")]
    assert parse_queue(QUEUE, 38) == [(4429795, "Jahmyr Gibbs")]


def test_pick_range_section_is_inclusive():
    for p in (90, 105, 120):
        assert (3052587, "Baker Mayfield") in parse_queue(QUEUE, p)
    assert (3052587, "Baker Mayfield") not in parse_queue(QUEUE, 121)


def test_candidates_put_the_queue_first_then_the_table(monkeypatch):
    # drafted_ids() reads the live pick feed; Gibbs went 1.03 for real on 2026-09-07
    monkeypatch.setattr(autodraft, "drafted_ids", set)
    state = {"recommend": {"top": [
        {"player_id": 1, "name": "Table Man", "score": 12.0},
        {"player_id": 4429795, "name": "Jahmyr Gibbs", "score": 30.0},  # already queued
        {"player_id": 2, "name": "Blacklisted", "score": -999.0},
    ]}}
    got = candidates(state, [(4429795, "Jahmyr Gibbs")])
    assert got == [(4429795, "Jahmyr Gibbs"), (1, "Table Man")]


def test_blacklisted_players_are_never_offered():
    state = {"recommend": {"top": [{"player_id": 2, "name": "QB4", "score": -999.0}]}}
    assert candidates(state, []) == []
