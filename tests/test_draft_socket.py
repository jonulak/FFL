"""Frame handling in ffl.draft_socket.DraftFeed (no browser involved)."""
from ffl.draft_socket import DraftFeed

ME, NEXT = 7, 6


def feed() -> DraftFeed:
    return DraftFeed(cache=None)


def test_selected_frames_accumulate_in_order():
    f = feed()
    f._handle("SELECTED 3 4361307 6")
    f._handle("SELECTED 7 3918298 0")
    assert [p["playerId"] for p in f.picks] == [4361307, 3918298]
    assert [p["overall"] for p in f.picks] == [1, 2]


def test_duplicate_frame_is_ignored():
    """Reconnecting replays frames; the same player must not be counted twice."""
    f = feed()
    f._handle("SELECTED 3 4361307 6")
    f._handle("SELECTED 3 4361307 6")
    assert len(f.picks) == 1


def test_on_clock_is_stale_until_the_next_selecting_arrives():
    """SELECTING lags the pick that ended the previous turn.

    Between our pick landing and the next SELECTING, the last frame still names *us*. Reporting
    that as "on the clock" told a driver it was our turn one pick too long, and it tried to
    draft out of turn (practice draft 3, pick 15). on_clock_now() withholds the stale value so
    the caller falls back to the snake order.
    """
    f = feed()
    f._handle(f"SELECTING {ME} 30000")
    assert f.on_clock_now() == ME

    f._handle("SELECTED 7 3918298 0")          # we pick; no SELECTING yet
    assert f.on_clock_now() is None, "stale on_clock reported as the current turn"

    f._handle(f"SELECTING {NEXT} 30000")       # the room catches up
    assert f.on_clock_now() == NEXT


def test_clock_frames_also_refresh_who_is_on_the_clock():
    f = feed()
    f._handle("SELECTED 7 3918298 0")
    f._handle(f"CLOCK 3 25000 {NEXT}")
    assert f.on_clock_now() == NEXT
