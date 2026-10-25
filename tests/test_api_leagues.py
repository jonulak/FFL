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


# sync and refresh per league, the home-only features refused (task 3)
HOME_ONLY = [
    ("get", "/outlook/1", "outlook"), ("post", "/outlook/1", "outlook"), ("get", "/waivers/outlook/1", "outlook"),
    ("post", "/waivers/outlook/1", "outlook"), ("post", "/outlook/refresh", "outlook"),
]


@pytest.mark.parametrize("method,path,feature", HOME_ONLY)
def test_home_only_routes_refuse_other_league(monkeypatch, client, other, method, path, feature):
    called = []
    for mod, name in ((api.espn_write, "pending_state"), (api.espn_write, "move_lineup"), (api.espn_write, "submit_claim"),
                      (api.outlook, "start"), (api.outlook, "start_one")):
        monkeypatch.setattr(mod, name, lambda *a, **k: called.append(name))
    r = client.request(method.upper(), path, params={"league": "espn:999"}, json={})
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "not_for_league", "league": "espn:999", "feature": feature}
    assert called == []


def test_refresh_other_league(monkeypatch, client, other):
    synced = []
    monkeypatch.setattr(api, "sync_all", lambda *, ref=None: synced.append(ref) or {"transactions": True})
    built = {"home": 0, "other": 0}

    def build(who):
        def fn():
            built[who] += 1
            return {"who": who}
        return fn
    api._serve("roster", build("home"))
    api._serve("roster", build("other"), ref=other)
    assert client.post("/refresh", params={"league": "espn:999"}).status_code == 200
    assert synced == [other]
    api._serve("roster", build("home"))
    api._serve("roster", build("other"), ref=other)
    assert built == {"home": 1, "other": 2}


def test_transactions_sync_other_league(monkeypatch, client, other):
    got = {}
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda ref=None, **k: f"LG:{ref.key}")
    monkeypatch.setattr(api, "load", lambda ref=None, **k: {"for": ref.key})
    monkeypatch.setattr(api, "sync_transactions", lambda lg, *, ref=None: got.update(tx=(lg, ref)) or [{}])
    monkeypatch.setattr(api, "sync_results", lambda lg, settings, *, ref=None: got.update(res=(lg, settings, ref)))
    r = client.post("/transactions/sync", params={"league": "espn:999"})
    assert r.status_code == 200 and r.json() == {"new": 1}
    assert got["tx"] == ("LG:espn:999", other)
    assert got["res"] == ("LG:espn:999", {"for": "espn:999"}, other)


# the write routes (ffl-soc.4.5 task 2): lineup, sit and claims on the open league, one lock per league
from ffl import espn_write  # noqa: E402

SWAP = {"moves": [{"player_id": 3, "to_slot": "RB/WR/TE"}, {"player_id": 2, "to_slot": "BE"}]}


@pytest.fixture
def writes(monkeypatch, other):
    """Builders and espn_write answer for any league and record the ref each was given."""
    seen = []
    cards = [{"player_id": 3, "name": "Bench", "pos": "RB", "slot": "BE", "call": "START", "gap": 1.4, "group": "bench", "locked": False},
             {"player_id": 2, "name": "Flex", "pos": "WR", "slot": "RB/WR/TE", "call": "BENCH", "gap": -1.4, "group": "starter", "locked": False},
             {"player_id": 11, "name": "Sit", "pos": "WR", "slot": "BE", "call": "BENCH", "gap": 0, "group": "bench", "locked": False, "reason": "r"}]
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    period = {}
    es, ew = api.espn_season, api.espn_write

    def rec(name, make=lambda *a: None):
        def fn(*a, ref=None, **k):
            seen.append((name, ref))
            return make(*a)
        return fn

    monkeypatch.setattr(es, "roster_state", rec("roster_state", lambda *a: {"week": 8, "live": False, "cards": cards}))
    monkeypatch.setattr(es, "fresh_league", rec("fresh_league"))
    monkeypatch.setattr(api.season, "roster_cards", lambda rows, settings, week: rows)
    monkeypatch.setattr(es, "roster_flags", rec("roster_flags", lambda lg, *a: {c["player_id"]: {"locked": False, "slot": c["slot"]} for c in cards}))
    monkeypatch.setattr(es, "load", rec("load", lambda *a: {"starters": {"RB/WR/TE": 1}}))
    monkeypatch.setattr(es, "forget_current_boxes", rec("forget_current_boxes"))
    monkeypatch.setattr(es, "free_agent_pool", rec("free_agent_pool", lambda *a: {"week": 8, "roster": roster, "positions": {"WR": [{"player_id": 101, "name": "A", "status": "WAIVERS"}]}}))
    monkeypatch.setattr(es, "pool_status", rec("pool_status", lambda *a: {101: {"status": "WAIVERS", "waiver_date": None}}))
    monkeypatch.setattr(api, "_roster_size", rec("roster_size", lambda *a: 17))
    monkeypatch.setattr(api, "_limits", rec("limits", lambda *a: {}))
    monkeypatch.setattr(ew, "writes", lambda: True)
    monkeypatch.setattr(ew, "pending_state", lambda max_age=0, *, ref=None: seen.append(("pending_state", ref)) or ([], period.get(ref.key if ref else None, 8)))
    monkeypatch.setattr(ew, "last_waiver_run", rec("last_waiver_run"))
    monkeypatch.setattr(ew, "pending_claims", rec("pending_claims", lambda: []))
    monkeypatch.setattr(ew, "move_lineup", lambda items, week, *, ref=None: seen.append(("move_lineup", ref)))
    for n in ("submit_claim", "cancel_claim", "add_free_agent", "reorder_claims"):
        monkeypatch.setattr(ew, n, rec(n))
    monkeypatch.setattr(ew, "fake_slots", rec("fake_slots", lambda: {}))
    return NS(seen=seen, period=period, cards=cards)


WRITE_CALLS = [("post", "/lineup", SWAP), ("post", "/roster/sit/11", {"sat": True}), ("get", "/claims", None),
               ("post", "/claims", {"groups": [{"targets": [101], "cuts": [1], "open": 0}]}),
               ("post", "/claims/submit", {"replace_outside": False, "expect_now": []})]


@pytest.mark.parametrize("method,path,body", WRITE_CALLS)
def test_write_routes_follow_the_league(monkeypatch, client, other, writes, method, path, body):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    r = client.request(method.upper(), path, params={"league": "espn:999"}, json=body)
    assert r.status_code == 200, r.text
    assert writes.seen, "the route reached its builders"
    assert all(ref == other for _, ref in writes.seen), [x for x in writes.seen if x[1] != other]


def test_the_lineup_write_is_other_leagues_alone(client, other, writes):
    assert client.post("/lineup?league=espn:999", json=SWAP).status_code == 200
    assert [n for n, _ in writes.seen].count("move_lineup") == 1
    assert ("move_lineup", other) in writes.seen


@pytest.mark.parametrize("name,method,path,body", [
    ("lineup", "post", "/lineup", SWAP), ("submit", "post", "/claims/submit", {"expect_now": []}),
    ("submit", "post", "/claims", {"groups": []})])
def test_locks_are_per_league(client, other, writes, name, method, path, body):
    lock = api._lock(name, leagues.home())
    assert lock is api._lock(name, leagues.home()) and api._lock(name, other) is api._lock(name, other)
    assert api._lock(name, other) is not lock
    assert lock.acquire(blocking=False)
    try:
        assert client.request(method.upper(), path, params={"league": "espn:999"}, json=body).status_code == 200
        assert client.request(method.upper(), path, json=body).status_code == 409
    finally:
        lock.release()
    held = api._lock(name, other)
    assert held.acquire(blocking=False)
    try:
        assert client.request(method.upper(), path, params={"league": "espn:999"}, json=body).status_code == 409
    finally:
        held.release()


def test_home_locks_keep_their_names():
    assert api._lock("lineup", leagues.home()) is api._LINEUP and api._lock("submit", leagues.home()) is api._SUBMIT


def test_write_decisions_land_under_their_league(monkeypatch, client, other, writes):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    r = client.post("/roster/sit/11?league=espn:999", json={"sat": True})
    assert r.status_code == 200 and r.json()["decision"]["id"]
    assert [d["title"].startswith("[fake]") for d in db.decisions(ref=other)] == [True]
    assert db.decisions() == []
    assert db.sat_ids(8, ref=other) == {11} and db.sat_ids(8) == set()


def test_roster_state_reads_its_league_fake_slots(monkeypatch, other):
    """FFL_WRITES=0: a fake move under other puts other's card in the new slot; home's card keeps ESPN's slot."""
    monkeypatch.setenv("FFL_WRITES", "0")
    espn_write.move_lineup([(1, "QB", "BE")], 3, ref=other)
    assert espn_write.fake_slots(ref=other) == {1: "BE"} and espn_write.fake_slots() == {}
    rows = [{"player_id": 1, "slot": "QB"}]
    flags = {1: {"slot": "QB", "order": 0, "locked": False}}
    slot = lambda fake: api.espn_season.with_espn_slots(rows, flags, fake)[0]["slot"]  # noqa: E731
    assert slot(espn_write.fake_slots(ref=other)) == "BE" and slot(espn_write.fake_slots()) == "QB"


def test_lineup_week_check_reads_its_league(client, other, writes):
    writes.period.update({leagues.home().key: 7, other.key: 8})
    assert client.post("/lineup?league=espn:999", json=SWAP).status_code == 200
    r = client.post("/lineup", json=SWAP)
    assert r.status_code == 409 and r.json()["detail"] == {"error": "ESPN's week changed — reload"}, "home's week is 7"


def test_submit_token_per_league(client, other, writes):
    r = client.post("/claims/submit?league=espn:999", json={"expect_now": [], "token": "tok"})
    assert r.status_code == 200
    assert client.get("/claims/submit/tok").status_code == 404
    got = client.get("/claims/submit/tok?league=espn:999").json()
    assert got == {"state": "done", "status": 200, "body": r.json()}


def test_claims_submit_runs_the_real_fake_store_under_its_league(monkeypatch, client, other):
    """FFL_WRITES=0, the real fake store: a plan with one free agent and one waiver target submits under other."""
    monkeypatch.setenv("FFL_WRITES", "0")
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    pool = {"WR": [{"player_id": 101, "name": "Addison", "status": "WAIVERS"}, {"player_id": 102, "name": "Mims", "status": "FREEAGENT"}]}
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None, **k: {"week": 6, "roster": roster, "positions": pool})
    monkeypatch.setattr(api.espn_season, "pool_status", lambda **k: {r["player_id"]: {"status": r["status"], "waiver_date": None} for r in pool["WR"]})
    monkeypatch.setattr(api, "_roster_size", lambda **k: 17)
    monkeypatch.setattr(api, "_limits", lambda **k: {})
    q = {"league": "espn:999"}
    assert client.post("/claims", params=q, json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]}).status_code == 200
    r = client.post("/claims/submit", params=q, json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 200, r.text
    fake, home = espn_write._fake(other), espn_write._fake(leagues.home())
    assert fake.adds and fake.claims
    assert not home.adds and not home.claims
    assert len(db.decisions(ref=other)) == 2 and all(d["title"].startswith("[fake]") for d in db.decisions(ref=other))
    assert db.decisions() == []
    plan = db.claim_plan(6, ref=other)
    assert plan["groups"] == [{"targets": [101], "cuts": [2], "open": 0}] and len(plan["submits"]) == 1
    assert db.claim_plan(6) is None


# ffl-soc.4.15: a league before its first week: ESPN's period (7) is behind the page's week (8), the league's first
def _first_week_8(monkeypatch, writes, other):
    sent = []
    writes.period[other.key] = 7
    monkeypatch.setattr(api.espn_season, "load", lambda *a, ref=None: {"starters": {"RB/WR/TE": 1}, "first_week": 8 if ref == other else 1})
    monkeypatch.setattr(api.espn_write, "move_lineup", lambda items, week, *, ref=None: sent.append(("lineup", week, ref)))
    monkeypatch.setattr(api.espn_write, "submit_claim", lambda a, d, week, *, ref=None: sent.append(("claim", week, ref)))
    return sent


def test_lineup_before_the_first_week_writes_espns_period(monkeypatch, client, other, writes):
    sent = _first_week_8(monkeypatch, writes, other)
    r = client.post("/lineup?league=espn:999", json=SWAP)
    assert r.status_code == 200, r.text
    assert sent == [("lineup", 7, other)], "the body takes ESPN's period, which it wants"


def test_claims_submit_before_the_first_week_writes_espns_period(monkeypatch, client, other, writes):
    sent = _first_week_8(monkeypatch, writes, other)
    q = {"league": "espn:999"}
    assert client.post("/claims", params=q, json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]}).status_code == 200
    r = client.post("/claims/submit", params=q, json={"replace_outside": False, "expect_now": []})
    assert r.status_code == 200, r.text
    assert sent == [("claim", 7, other)]


def test_home_week_check_is_unchanged(monkeypatch, client, other, writes):
    _first_week_8(monkeypatch, writes, other)
    writes.period[leagues.home().key] = 7  # home's page is on 8
    r = client.post("/lineup", json=SWAP)
    assert r.status_code == 409 and r.json()["detail"] == {"error": "ESPN's week changed — reload"}
