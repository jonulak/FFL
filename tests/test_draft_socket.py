"""Frame handling in ffl.draft_socket.DraftFeed (no browser involved)."""
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from ffl import db, leagues
from ffl.draft_socket import DraftFeed, FeedStore
from tests.home import home_league

ME, NEXT = 7, 6


def feed() -> DraftFeed:
    return DraftFeed("100000001")


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


class _Page:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False

    def on(self, *a):
        pass


def _cdp(pages):
    """A fake CDP link to a Chrome holding `pages`: what connect() reads, nothing more."""
    session = SimpleNamespace(on=lambda *a: None, send=lambda *a: None)
    ctx = SimpleNamespace(pages=pages, new_cdp_session=lambda page: session)
    browser = SimpleNamespace(contexts=[ctx])
    return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda url: browser))


def test_feed_attaches_only_to_its_league():
    room = "https://fantasy.espn.com/football/draft?leagueId={}&seasonId=2026&teamId=1"
    pages = [_Page(room.format(111)), _Page(room.format(222)), _Page(room.format(2222)),
             _Page("https://fantasy.espn.com/football/league?leagueId=222")]
    f = DraftFeed("222")
    f._pw = _cdp(pages)
    assert f.connect() == pages[1].url
    f = DraftFeed("333")
    f._pw = _cdp(pages)
    with pytest.raises(RuntimeError, match="333"):
        f.connect()


class _FlakyStore:
    def __init__(self, fails: int):
        self.fails, self.saved = fails, None

    def load(self):
        return [], 0

    def save(self, picks, offset):
        if self.fails:
            self.fails -= 1
            raise sa.exc.OperationalError("INSERT", {}, Exception("database is locked"))
        self.saved = (list(picks), offset)


def test_a_failed_save_warns_once_and_the_next_pump_retries(capsys):
    store = _FlakyStore(fails=2)
    f = DraftFeed("222", store=store)
    f._page = SimpleNamespace(wait_for_timeout=lambda ms: None)
    f._handle("SELECTED 4 12 0")                 # the save fails: warned and flagged, not raised
    assert f.dirty and store.saved is None and "not saved" in capsys.readouterr().out
    assert f.pump(0) and f.dirty, "still failing: still dirty"
    assert "not saved" not in capsys.readouterr().out, "one warning per outage, not one a second"
    assert f.pump(0) and not f.dirty
    assert store.saved == ([{"teamId": 4, "playerId": 12, "lineupSlotId": 0, "overall": 1}], 0)


def test_feed_resumes_from_the_db():
    """A watcher restarted mid-draft starts from the picks its league's feed already holds."""
    ref = home_league()
    stored = [{"teamId": 3, "playerId": 11, "lineupSlotId": 0, "overall": 1}]
    db.put_draft_feed(ref, stored, 2)
    f = DraftFeed(ref.provider_id, store=FeedStore(ref))
    assert f.picks == stored and f.offset == 2
    f._handle("SELECTED 4 12 0")
    assert db.draft_feed(ref) == (stored + [{"teamId": 4, "playerId": 12, "lineupSlotId": 0, "overall": 2}], 2)
