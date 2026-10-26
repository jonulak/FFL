"""The queue file is edited between turns, sometimes seconds before a pick, so its parsing
has to be forgiving of comments and blank lines and exact about which picks a section covers.
"""
from ffl import autodraft, db, leagues
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
    monkeypatch.setattr(autodraft, "drafted_ids", lambda ref=None: set())
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


def _other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, False, None, None))


def test_drafted_ids_come_from_the_leagues_feed():
    other = _other()
    db.put_draft_feed(other, [{"teamId": 1, "playerId": 4429795, "lineupSlotId": 2, "overall": 1}], 0)
    assert autodraft.drafted_ids(other) == {4429795}
    assert autodraft.drafted_ids(leagues.home()) == set()
    state = {"recommend": {"top": [{"player_id": 4429795, "name": "Jahmyr Gibbs", "score": 30.0}]}}
    assert candidates(state, [(4429795, "Jahmyr Gibbs")], other) == []
    assert candidates(state, [], leagues.home()) == [(4429795, "Jahmyr Gibbs")]


def test_confirm_drafted_treats_a_busy_db_as_not_yet(monkeypatch):
    import sqlalchemy as sa
    from ffl import draft_pick
    other = _other()
    db.put_draft_feed(other, [{"teamId": 1, "playerId": 4429795, "lineupSlotId": 2, "overall": 1}], 0)
    real, calls = db.draft_feed, []

    def flaky(ref):
        calls.append(1)
        if len(calls) == 1:
            raise sa.exc.OperationalError("SELECT", {}, Exception("database is locked"))
        return real(ref)
    monkeypatch.setattr(db, "draft_feed", flaky)
    monkeypatch.setattr(draft_pick.time, "sleep", lambda s: None)
    assert draft_pick.confirm_drafted(4429795, timeout_s=5, ref=other) is True and len(calls) == 2
    assert draft_pick.confirm_drafted(1, timeout_s=5, ref=leagues.home()) is None, "no feed running"


def test_the_turn_is_read_off_the_leagues_draft_state():
    other = _other()
    turn = lambda ref, last: autodraft.my_turn(autodraft._state(ref), last)
    assert turn(other, None) is None, "no state yet: wait"
    db.put_draft_state(other, {"current_pick": 12, "on_clock_is_me": True, "drafted": False})
    assert turn(other, None) == 12
    assert turn(other, 12) is None, "a turn already taken is not taken twice"
    assert turn(leagues.home(), None) is None, "another league's turn is not mine"
    db.put_draft_state(other, {"current_pick": 13, "on_clock_is_me": False, "drafted": False})
    assert turn(other, 12) is None
    db.put_draft_state(other, {"current_pick": 14, "on_clock_is_me": True, "drafted": True})
    assert turn(other, 12) is None, "the draft is over"


def test_practice_db_is_exactly_the_mock():
    from ffl.autodraft import practice_db
    assert practice_db("postgresql+psycopg:///ffl_mock")
    assert practice_db("postgresql+psycopg://u@localhost:5432/ffl_mock")
    for bad in ("postgresql+psycopg:///ffl_real?host=/tmp/mock", "postgresql+psycopg:///ffl_mock_x", "postgresql+psycopg:///ffl_mockery",
                "postgresql+psycopg://mock@host/ffl_prod", "", None, "not a url"):
        assert not practice_db(bad), bad
