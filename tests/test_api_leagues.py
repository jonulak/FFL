"""The open league (?league=), a page cache per league, and move listeners per league (ffl-soc.4.4)."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ffl import api, db, journal, leagues


@pytest.fixture
def other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, False, None, None))


@pytest.fixture
def client():
    return TestClient(api.app)


def _stub_roster(monkeypatch):
    seen = []

    def roster_state(week=None, **k):
        seen.append(k.get("ref"))
        return {"week": 3, "cards": []}
    monkeypatch.setattr(api.espn_season, "roster_state", roster_state)
    monkeypatch.setattr(api.outlook, "attach", lambda cards, *a, **k: cards)
    monkeypatch.setattr(api.outlook, "load", lambda *a, **k: {})
    return seen


def test_open_league_resolves(other):
    assert api.open_league(None) == leagues.home()
    assert api.open_league("") == leagues.home()
    assert api.open_league("espn:999") == other
    assert api.open_league(leagues.home().key) == leagues.home()


def test_unknown_league_404(client):
    r = client.get("/roster?league=espn:404")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "unknown_league", "league": "espn:404"}


def test_not_for_league(other):
    api._not_for_league(leagues.home(), "claims")
    with pytest.raises(HTTPException) as e:
        api._not_for_league(other, "claims")
    assert e.value.status_code == 404
    assert e.value.detail == {"error": "not_for_league", "league": "espn:999", "feature": "claims"}


def test_empty_league_param_is_home(client, monkeypatch):
    _stub_roster(monkeypatch)
    assert client.get("/roster?league=").status_code == 200
    assert api.cache.has("roster")


def test_explicit_home_param_is_served_like_none(client, monkeypatch):
    _stub_roster(monkeypatch)
    assert client.get(f"/roster?league={leagues.home().key}").status_code == 200
    assert api.cache.has("roster")
    assert list(api.caches) == [leagues.home().key]


def test_cache_for(other):
    assert api.cache_for(None) is api.cache
    assert api.cache_for(leagues.home()) is api.cache
    c = api.cache_for(other)
    assert c is api.cache_for(other) and c is not api.cache


def test_same_key_two_leagues_never_cross(other):
    n = {"home": 0, "other": 0}

    def build(who):
        def fn():
            n[who] += 1
            return {"who": who}
        return fn
    for _ in range(2):
        assert api._serve("roster", build("home"))["who"] == "home"
        assert api._serve("roster", build("other"), ref=other)["who"] == "other"
    assert n == {"home": 1, "other": 1}


def test_other_league_pages_persist_under_its_key(other):
    api._serve("roster", lambda: {"who": "other"}, ref=other)
    assert [p["key"] for p in db.pages(ref=other)] == ["roster"] and db.pages() == []
    fresh = api.Cache(api.TTL, persist=True, ref=other)
    fresh.load()
    payload, _ = fresh.get_or_build("roster", lambda: {"who": "rebuilt"})
    assert payload == {"who": "other"}


def test_other_league_rows_drop_only_its_pages(monkeypatch, other):
    act = NS(date=1788937502756, actions=[(NS(team_id=3, team_name="T3"), "FA ADDED", NS(playerId=77, name="Guy"), 0)])
    monkeypatch.setattr(journal, "_activities", lambda lg, size: [act])
    built = {"home": 0, "other": 0}

    def build(who):
        def fn():
            built[who] += 1
            return {"who": who}
        return fn
    api._serve("roster", build("home"))
    api._serve("roster", build("other"), ref=other)
    assert len(journal.sync_transactions(NS(), ref=other)) == 1
    api.cache_for(other).join()  # the move dropped other's page and rewarmed it behind; home's stood
    assert built == {"home": 1, "other": 2}
    assert db.unsent_notices() == [] and db.unsent_notices(ref=other) == []
