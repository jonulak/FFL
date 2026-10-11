"""FleaFlicker's current-week projections (fixtures, no network)."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ffl import sources
from ffl.sources import fleaflicker

FIX = Path(__file__).parent / "fixtures" / "sources"
QB = (FIX / "fleaflicker_w5_qb.html").read_text()
RB = (FIX / "fleaflicker_w5_rb.html").read_text()
KS = {"ppr": 1.0, "pass_td": 4, "pass_yd": 0.04, "int": -2, "rush_yd": 0.1, "rush_td": 6,
      "rec_yd": 0.1, "rec_td": 6, "fumble": -2}


def test_qb_rows_rescored_and_played_games_skipped():
    out = fleaflicker.parse_page(QB, "QB", KS, 5)
    # Maye: 245.3 yds, 2 TD, 0.8 INT; rush 31.3 yds, 0.2 TD
    assert out[("drake maye", "QB")] == pytest.approx(245.3 * .04 + 8 - 1.6 + 3.13 + 1.2, abs=.06)
    assert ("dak prescott", "QB") not in out, "his game was over: the row holds actual stats"


def test_rb_rows_read_receiving_and_fumbles_by_group():
    out = fleaflicker.parse_page(RB, "RB", KS, 5)
    # Gibbs: rush 96.6 yds 1 TD; rec 4.7, 39.9 yds, .4 TD; fum .1
    assert out[("jahmyr gibbs", "RB")] == pytest.approx(9.66 + 6 + 4.7 + 3.99 + 2.4 - .2, abs=.06)


def test_another_week_is_nothing():
    assert fleaflicker.parse_page(QB, "QB", KS, 6) == {}, "the page shows week 5 whatever was asked"


def test_a_reshaped_page_is_nothing():
    assert fleaflicker.parse_page("<html><body>down</body></html>", "QB", KS, 5) == {}
    assert fleaflicker.parse_page(QB.replace("Passing", "Throwing"), "QB", KS, 5) == {}


def test_fetch_week_walks_the_pages(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"QB": 1, "RB": 1})
    urls = []

    def fake_get(url, **kw):
        urls.append(url)
        text = QB if "position=4&" in url else RB if "position=1&" in url else "<html></html>"
        return SimpleNamespace(raise_for_status=lambda: None, text=text)

    monkeypatch.setattr(fleaflicker.requests, "get", fake_get)
    out = fleaflicker.fetch_week(5, KS)
    assert ("drake maye", "QB") in out and ("jahmyr gibbs", "RB") in out
    assert len(urls) == sum(fleaflicker.PAGES.values())
    assert any("tableOffset=20" in u for u in urls)
