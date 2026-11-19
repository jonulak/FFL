"""FantasyCalc's market values (no network): the league's format, the parse, and what an outage leaves."""
from types import SimpleNamespace

import pytest
import requests

from ffl import sources
from ffl.sources import fantasycalc

FORMAT = {"numQbs": 2, "numTeams": 10, "ppr": 1}


def row(espn_id, pos, value):
    return {"player": {"espnId": espn_id, "position": pos, "name": "x"}, "value": value}


def full_answer():
    """Enough rows at each position to pass the thin-scrape guard, player 1 a 7000 QB."""
    return [row("1", "QB", 7000), *[row(str(i), pos, 100) for pos, start in (("QB", 100), ("RB", 200), ("WR", 300), ("TE", 400))
                                    for i in range(start, start + 20)]]


def settings(starters=None, teams=10, rec=1.0):
    return {"starters": starters or {"QB": 2}, "team_count": teams,
            "scoring_tables": {p: {"rec": rec} for p in ("QB", "RB", "WR", "TE")}}


@pytest.fixture(autouse=True)
def fresh():
    sources.reset()
    yield
    sources.reset()


def test_parse_reads_espns_id_as_a_number_and_skips_a_player_without_one():
    assert fantasycalc.parse([row("4040715", "QB", 10888), row(None, "RB", 50)]) == {(4040715, "QB"): 10888}


@pytest.mark.parametrize("starters,qbs", [({"QB": 2}, 2), ({"QB": 1, "OP": 1}, 2), ({"QB": 1}, 1)])
def test_format_counts_a_second_qb_or_an_op_slot(starters, qbs):
    assert fantasycalc.league_format(settings(starters))["numQbs"] == qbs


@pytest.mark.parametrize("teams,asked", [(8, 8), (10, 10), (12, 12), (14, 14), (16, 14), (6, 8)])
def test_format_snaps_the_league_size_since_any_other_quietly_answers_as_12(teams, asked):
    assert fantasycalc.league_format(settings(teams=teams))["numTeams"] == asked


@pytest.mark.parametrize("rec,asked", [(0.0, 0), (0.5, 0.5), (1.0, 1), (0.4, 0.5), (1.5, 1)])
def test_format_takes_the_nearest_ppr(rec, asked):
    assert fantasycalc.league_format(settings(rec=rec))["ppr"] == asked


def test_values_asks_for_redraft_in_the_leagues_format_once(monkeypatch):
    calls = []

    def get(url, params, **kw):
        calls.append(params)
        return SimpleNamespace(raise_for_status=lambda: None, json=full_answer)
    monkeypatch.setattr(fantasycalc.requests, "get", get)
    assert fantasycalc.values(FORMAT)[1] == 7000
    fantasycalc.values(FORMAT)
    assert calls == [{"isDynasty": "false", **FORMAT}], "cached: one call, and never dynasty values"


def test_values_is_empty_when_the_fetch_fails_with_nothing_saved(monkeypatch):
    def down(*a, **k):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(fantasycalc.requests, "get", down)
    assert fantasycalc.values(FORMAT) == {}


def test_values_switched_off_on_this_host_asks_nobody(monkeypatch):
    def never(*a, **k):
        raise AssertionError("asked a source switched off")
    monkeypatch.setattr(fantasycalc.requests, "get", never)
    monkeypatch.setenv("FFL_SOURCES_OFF", "fantasycalc")
    assert fantasycalc.values(FORMAT) == {}


def test_totals_adds_each_side_and_counts_who_has_no_value():
    market = {1: 7000, 2: 300, 3: 5000}
    assert fantasycalc.totals([1, 2], [3, -16001], market) == {"send": 7300, "get": 5000, "unpriced": 1}


def test_totals_is_none_with_no_values_at_all():
    assert fantasycalc.totals([1], [3], {}) is None
