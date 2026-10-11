"""FantasySharks CSV projections rescored to the league (fixtures, no network)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ffl import sources
from ffl.sources import sharks

FIX = Path(__file__).parent / "fixtures" / "sources"
KS = {"ppr": 1.0, "pass_td": 4, "pass_yd": 0.04, "int": -2, "rush_yd": 0.1, "rush_td": 6,
      "rec_yd": 0.1, "rec_td": 6, "fumble": -2}


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
    assert sharks.segment("ros", 2026) == 877
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
