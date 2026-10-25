"""FantasySharks CSV projections rescored to the league (fixtures, no network)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ffl import sources
from ffl.sources import sharks
from tests.scoring import tables

FIX = Path(__file__).parent / "fixtures" / "sources"
KS = tables({"ppr": 1.0, "pass_td": 4, "pass_yd": 0.04, "int": -2, "rush_yd": 0.1, "rush_td": 6,
      "rec_yd": 0.1, "rec_td": 6, "fumble": -2})


def test_weekly_qb_rescored_from_raw_stats():
    out = sharks.parse_csv((FIX / "sharks_888_qb.csv").read_text(), "QB", KS)
    # Allen: 247 yds, 2.1 TD, 0.6 INT, 34 rush yds, 1.2 rush TD, 0.2 fum lost
    assert out[("josh allen", "QB")] == pytest.approx(247 * .04 + 2.1 * 4 - .6 * 2 + 3.4 + 1.2 * 6 - .2 * 2, abs=.05)
    assert len(out) >= 50


def test_weekly_rb_rescored():
    out = sharks.parse_csv((FIX / "sharks_888_rb.csv").read_text(), "RB", KS)
    # Robinson: 92 rush yds, .7 TD, 3.7 rec, 33 rec yds, .2 rec TD, .1 fum lost
    assert out[("bijan robinson", "RB")] == pytest.approx(9.2 + 4.2 + 3.7 + 3.3 + 1.2 - .2, abs=.05)


def test_ros_rb_reverses_last_first_with_a_suffix():
    out = sharks.parse_csv((FIX / "sharks_877_rb.csv").read_text(), "RB", KS)
    assert out[("kenneth walker", "RB")] == pytest.approx(111.3 + 7.4 * 6 + 30.1 + 22.8 + 3.1 * 6 - .4, abs=.05)


def test_a_reshaped_file_is_nothing():
    text = (FIX / "sharks_888_rb.csv").read_text().replace("Rush Yds", "Rushing")
    assert sharks.parse_csv(text, "RB", KS) == {}
    assert sharks.parse_csv("", "RB", KS) == {}


def test_segments():
    assert sharks.segment(5, 2026) == 887 and sharks.segment(6, 2026) == 888
    with pytest.raises(ValueError):
        sharks.segment(6, 2031)


def test_fetch_week_asks_each_position_for_the_week(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"QB": 1, "RB": 1})
    urls = []
    qb, rb = (FIX / "sharks_888_qb.csv").read_text(), (FIX / "sharks_888_rb.csv").read_text()

    def fake_get(url, **kw):
        urls.append(url)
        text = qb if "Position=1&" in url else rb if "Position=2&" in url else "Rank,Player Name\n"
        return SimpleNamespace(raise_for_status=lambda: None, text=text)

    monkeypatch.setattr(sharks.requests, "get", fake_get)
    out = sharks.fetch_week(6, KS)
    assert ("josh allen", "QB") in out and ("bijan robinson", "RB") in out
    assert all("Segment=888" in u for u in urls) and len(urls) == 4


def test_fetch_ros_sums_the_weekly_projections_for_w_through_18(monkeypatch):
    asked = []

    def week(w, ks, ttl=None):
        asked.append(w)
        d = {("a b", "WR"): 10.0}
        if w != 7:
            d[("c d", "RB")] = 1.0   # bye week: absent that week, the rest still sum
        return d

    monkeypatch.setattr(sources, "MIN_ROWS", {"WR": 1})
    monkeypatch.setattr(sharks, "fetch_week", week)
    out = sharks.fetch_ros(KS, 5)
    assert sorted(asked) == list(range(5, 19))
    assert out[("a b", "WR")] == pytest.approx(140.0)
    assert out[("c d", "RB")] == pytest.approx(13.0)


def test_fetch_ros_is_empty_when_any_week_is_missing(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"WR": 1})
    monkeypatch.setattr(sharks, "fetch_week", lambda w, ks, ttl=None: {} if w == 12 else {("a b", "WR"): 10.0})
    assert sharks.fetch_ros(KS, 5) == {}
    st = sources.STATUS["sharks-ros"]
    assert st["failing_since"] and "12" in st["error"]
    assert "sharks" not in sources.STATUS, "the weekly record is untouched"


def test_future_weeks_are_refetched_every_six_hours_not_every_poll(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"WR": 1})
    clock = [1000.0]
    monkeypatch.setattr(sources.time, "time", lambda: clock[0])
    asked = []
    monkeypatch.setattr(sharks, "_fetch", lambda seg, ks: asked.append(seg) or {("a b", "WR"): 1.0})
    sharks.fetch_ros(KS, 17)
    now, future = sharks.segment(17), sharks.segment(18)
    assert sorted(asked) == [now, future]
    clock[0] += sources.TTL_S + 1
    sharks.fetch_ros(KS, 17)
    assert sorted(asked) == [now, now, future], "only the first week came due"
    clock[0] += sharks.FUTURE_TTL_S
    sharks.fetch_ros(KS, 17)
    assert sorted(asked) == [now, now, now, future, future]
