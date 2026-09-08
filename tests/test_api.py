"""API glue: caching, stale fallback, validation. espn_season is stubbed; no network."""
import time

import pytest
from fastapi.testclient import TestClient

from ffl import api


@pytest.fixture
def client(monkeypatch, tmp_path):
    calls = {"week": 0}

    def fake_week(week=None):
        calls["week"] += 1
        return {"week": week or 3, "live": False, "me": {"team": "me"}, "opp": {"team": "opp"}, "stale": None}

    monkeypatch.setattr(api.espn_season, "week_state", fake_week)
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: {"week": 3, "positions": {"RB": []}})
    monkeypatch.setattr(api.espn_season, "league_state", lambda: {"week": 3, "standings": [], "scoreboard": [], "power": []})
    monkeypatch.setattr(api.espn_season, "journal_state", lambda: {"decisions_md": "# x", "transactions": []})
    monkeypatch.setattr(api.espn_season, "draft_recap", lambda: {"picks": [], "recap_md": ""})
    monkeypatch.setattr(api, "sync_all", lambda: {"transactions": 0})
    dec = tmp_path / "DECISIONS.md"
    dec.write_text("# Decisions log\n")
    monkeypatch.setattr(api, "DECISIONS", dec)
    api.cache.clear()
    c = TestClient(api.app)
    c.calls = calls
    return c


def test_week_is_cached_within_ttl(client):
    assert client.get("/week/3").json()["me"]["team"] == "me"
    client.get("/week/3")
    assert client.calls["week"] == 1
    assert client.get("/week").json()["week"] == 3


def test_refresh_busts_cache(client):
    client.get("/week/3")
    api.espn_season._SL[3] = (time.time() + 1800, {})
    api.espn_season._SEASON_CACHE[:] = [time.time(), {}]
    r = client.post("/refresh")
    assert r.status_code == 200 and "synced" in r.json()
    assert not api.espn_season._SL and not api.espn_season._SEASON_CACHE
    client.get("/week/3")
    assert client.calls["week"] == 2


def test_stale_payload_served_when_source_fails(client, monkeypatch):
    client.get("/week/3")
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    api.cache.expire_all()
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["stale_since"] is not None


def test_502_when_nothing_cached(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "league_state", lambda: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.get("/league")
    assert r.status_code == 502 and r.json()["detail"]["source"] == "espn"


def test_decisions_validation_and_append(client):
    r = client.post("/decisions", json={"title": "", "decision": "d", "reasoning": "r", "alternatives": ""})
    assert r.status_code == 422
    r = client.post("/decisions", json={"title": "T", "decision": "d", "reasoning": "r", "alternatives": "a"})
    assert r.status_code == 200 and "## " in r.json()["decisions_md"] and "— T" in r.json()["decisions_md"]


def test_waivers_requires_known_pos(client):
    assert client.get("/waivers?pos=RB").status_code == 200
    assert client.get("/waivers?pos=XX").status_code == 422


def test_health_and_journal_and_draft(client):
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/journal").json()["stale_since"] is None
    assert client.get("/draft").json()["stale_since"] is None
