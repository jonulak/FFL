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
    seen = _stub_roster(monkeypatch)
    assert client.get("/roster?league=").status_code == 200
    assert api.cache.has("roster")
    assert seen == [leagues.home()]


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



# the read routes (task 2): each takes the league it is asked for
@pytest.fixture
def two(monkeypatch, other):
    """The espn_season builders answer with the league they were asked for, and record each ref."""
    seen = []
    es = api.espn_season

    def stub(name, make):
        def fn(*a, ref=None, **k):
            seen.append((name, ref))
            return {"league": ref.key if ref else None, **make(*a)}
        monkeypatch.setattr(es, name, fn)

    side = lambda tid: {"team_id": tid, "lineup": [{"player_id": 11, "actual": 1.0, "team": "DET"}], "bench": [], "ir": []}  # noqa: E731
    stub("week_state", lambda week=None, *a, **k: {"week": 3, "live": False, "me": side(7), "opp": side(3)})
    stub("team_state", lambda tid, *a: {"week": 3, "live": False, "mine": False, "club": {"team_id": tid, "rank": 1}, "side": side(tid)})
    stub("player_card", lambda pid: {"current_week": 3, "team": "DET", "player_id": pid})
    stub("free_agent_pool", lambda *a: {"week": 3, "positions": {"RB": []}, "watched": []})
    stub("league_state", lambda week=None: {"week": 3, "standings": [], "scoreboard": []})
    stub("transactions_state", lambda: {"moves": [], "teams": []})
    stub("roster_state", lambda *a: {"week": 3, "live": False, "cards": [{"player_id": 11, "name": "A"}]})
    monkeypatch.setattr(es, "draft_picks", lambda *, ref=None: seen.append(("draft_picks", ref)) or [])
    monkeypatch.setattr(api.outlook, "load", lambda *a, **k: {})
    monkeypatch.setattr(api.outlook, "written", lambda *a, **k: set())
    return seen


ROUTES = ["/week", "/week/3", "/team/3", "/player/11", "/players", "/league", "/transactions", "/roster", "/watch",
          "/player/11/transactions"]


@pytest.mark.parametrize("path", ROUTES)
def test_read_routes_serve_the_open_league(client, two, other, path):
    body = client.get(f"{path}?league=espn:999").json()
    assert two and {ref for _, ref in two} == {other}
    if path not in ("/players", "/watch", "/player/11/transactions"):  # these answer their pool's or log's own shape
        assert body["league"] == "espn:999"
    two.clear()
    client.get(path)
    assert {ref for _, ref in two} == {leagues.home()}


def test_db_routes_serve_the_open_league(client, other):
    db.put_watch(11, True, ref=other)
    db.put_league_state("decision_prefs", {"log": False}, ref=other)
    db.add_decision({"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app", "title": "T",
                     "decision": "d", "rationale": None, "player_ids": [11]}, ref=other)
    q = "?league=espn:999"
    assert client.get(f"/watch/ids{q}").json() == {"ids": [11]} and client.get("/watch/ids").json() == {"ids": []}
    assert client.get(f"/decisions/prefs{q}").json()["log"] is False and client.get("/decisions/prefs").json()["log"] is True
    assert len(client.get(f"/decisions{q}").json()["decisions"]) == 1 and client.get("/decisions").json()["decisions"] == []
    assert client.post(f"/decisions/prefs{q}", json={"page": False}).json() == {"log": False, "page": False}
    assert client.get("/decisions/prefs").json() == {"log": True, "page": True}


def test_other_league_has_no_live_or_feed(client, two, monkeypatch):
    monkeypatch.setattr(api.live.STATE, "points", {11: 9.5})
    monkeypatch.setattr(api.live.STATE, "week", 3)
    monkeypatch.setattr(api, "_plays", lambda data, limit=None: [{"delta": 1.0}])
    theirs = client.get("/week?league=espn:999").json()
    assert theirs["me"]["lineup"][0]["actual"] == 1.0 and theirs["plays"] == []
    mine = client.get("/week").json()
    assert mine["me"]["lineup"][0]["actual"] == 9.5 and mine["plays"] == [{"delta": 1.0}]
    feed = client.get("/plays?league=espn:999").json()
    assert feed["plays"] == [] and feed["live"] is False
    assert client.get("/team/3?league=espn:999").json()["side"]["lineup"][0]["actual"] == 1.0
    assert client.get("/team/3").json()["side"]["lineup"][0]["actual"] == 9.5


def test_other_league_roster_has_no_outlooks(client, two, monkeypatch):
    def attach(cards, *a, **k):
        return [{**c, "outlook": {"bullets": ["x"]}} for c in cards]
    monkeypatch.setattr(api.outlook, "attach", attach)
    assert "outlook" in client.get("/roster").json()["cards"][0]
    body = client.get("/roster?league=espn:999").json()
    assert "outlook" not in body["cards"][0] and body["outlook_run"]["running"] is False


def test_watch_per_league(client, two, other):
    assert client.post("/watch/11?league=espn:999", json={"watched": True}).json() == {"watched": True, "ids": [11]}
    assert db.watched_ids(ref=other) == [11] and db.watched_ids() == []


def test_presets_per_league(client, other):
    pid = client.post("/players/presets?league=espn:999", json={"name": "Mine", "pos": "ALL", "fa": True, "cols": ["snap_pct"], "filters": [],
                                                                   "sort": {"id": "snap_pct", "desc": True}}).json()["id"]
    assert pid in {p["id"] for p in client.get("/players/presets?league=espn:999").json()["presets"]}
    assert pid not in {p["id"] for p in client.get("/players/presets").json()["presets"]}


def test_decision_other_league_404(client, other):
    i = db.add_decision({"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app", "title": "T",
                         "decision": "d", "rationale": None, "player_ids": []})
    assert client.post(f"/decisions/{i}?league=espn:999", json={"outcome": "ok"}).status_code == 404
    assert client.post(f"/decisions/{i}", json={"outcome": "ok"}).status_code == 200
