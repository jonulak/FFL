"""CBS rest-of-season projections: the page parser (fixtures) and the cached fetch (no network)."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from ffl import sources
from ffl.sources import cbs

FIX = Path(__file__).parent / "fixtures" / "sources"
RB = (FIX / "cbs_ros_rb.html").read_text()
# half-PPR-ish league keys, the shape settings.key_scoring returns
KS = {"ppr": 0.5, "pass_td": 4, "pass_yd": 0.04, "int": -2, "rush_yd": 0.1, "rush_td": 6,
      "rec_yd": 0.1, "rec_td": 6, "fumble": -2}


def test_parse_rb_page_rescores_raw_stats_to_league_scoring():
    out = cbs.parse_page(RB, "RB", KS)
    gibbs = out[("jahmyr gibbs", "RB")]
    assert gibbs["games"] == 16
    assert gibbs["pts"] == pytest.approx(1621 * 0.1 + 17 * 6 + 70 * 0.5 + 610 * 0.1 + 5 * 6 - 3 * 2, abs=0.05)
    assert gibbs["pts"] != pytest.approx(418.4), "CBS's own fpts column is PPR, not this league"
    sampson = out[("dylan sampson", "RB")]
    assert sampson["games"] == 12, "games left, byes and injury weeks already out"
    assert sampson["pts"] == pytest.approx(216 * 0.1 + 6 + 30 * 0.5 + 200 * 0.1 + 6, abs=0.05), "a dash reads as 0"


def test_parse_page_returns_empty_on_a_reshaped_page():
    assert cbs.parse_page("<html><body>maintenance</body></html>", "RB", KS) == {}
    assert cbs.parse_page(RB.replace("sortcol=rushing_yds", "sortcol=renamed"), "RB", KS) == {}, \
        "a column the scorer needs has gone: better nothing than a wrong number"


def test_pages_are_the_four_positions_cbs_scores_like_the_league():
    assert set(cbs.PAGES) == {"QB", "RB", "WR", "TE"}, "K and D/ST are on CBS's own scale: Sleeper alone"


def test_fetch_ros_serves_last_good_when_cbs_goes_down(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if len(calls) <= len(cbs.PAGES):
            return SimpleNamespace(raise_for_status=lambda: None, text=RB if "/RB/" in url else "<html></html>")
        raise requests.ConnectionError("cbs down")

    monkeypatch.setattr(cbs.requests, "get", fake_get)
    good = cbs.fetch_ros(KS)
    assert ("jahmyr gibbs", "RB") in good
    sources.expire_all()
    assert cbs.fetch_ros(KS) == good, "CBS down: the stale data still serves"


WEEK_RB = (FIX / "cbs_w5_rb.html").read_text()


def test_weekly_page_parses_with_the_same_parser():
    out = cbs.parse_page(WEEK_RB, "RB", KS)
    assert len(out) >= 60
    assert all(v["games"] in (0, 1) for v in out.values())


def test_fetch_week_skips_rows_without_a_game(monkeypatch):
    monkeypatch.setattr(sources, "MIN_ROWS", {"RB": 1})
    monkeypatch.setattr(cbs.requests, "get", lambda url, **kw: SimpleNamespace(
        raise_for_status=lambda: None, text=WEEK_RB if "/RB/" in url else "<html></html>"))
    week = cbs.fetch_week(5, KS)
    parsed = cbs.parse_page(WEEK_RB, "RB", KS)
    assert set(week) == {k for k, v in parsed.items() if v["games"] > 0}
    assert "/2026/5/projections/ppr/" in cbs.WEEK_URL.format(pos="RB", season=2026, week=5)
