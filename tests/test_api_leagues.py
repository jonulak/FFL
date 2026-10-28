"""The open league (?league=), a page cache per league, and move listeners per league (ffl-soc.4.4)."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ffl import api, db, journal, leagues
from tests.home import HOME_KEY, home_league


@pytest.fixture
def other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)


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
    from tests.conftest import TEST_USER
    for user in (TEST_USER, None):  # the signed-in owner's memberships; a job's request (no user) through the owner
        req = NS(scope={"state": {"user": user}})
        if user is None:  # a job names its league
            for bare in (None, ""):
                with pytest.raises(api.HTTPException) as e:
                    api.open_league(req, bare)
                assert e.value.status_code == 400 and e.value.detail == {"error": "league_required"}
        else:  # none, or empty, is the user's default
            assert api.open_league(req, None) == api.open_league(req, "") == home_league()
        assert api.open_league(req, "espn:999") == other
        assert api.open_league(req, home_league().key) == home_league()


def test_unknown_league_404(client):
    r = client.get("/roster?league=espn:404")
    assert r.status_code == 404
    assert r.json()["detail"] == {"error": "unknown_league", "league": "espn:404"}


def test_empty_league_param_is_home(client, monkeypatch):
    seen = _stub_roster(monkeypatch)
    assert client.get("/roster?league=").status_code == 200
    assert api.cache_for(home_league()).has("roster")
    assert seen == [home_league()]


def test_explicit_home_param_is_served_like_none(client, monkeypatch):
    _stub_roster(monkeypatch)
    assert client.get(f"/roster?league={home_league().key}").status_code == 200
    assert api.cache_for(home_league()).has("roster")
    assert list(api.caches) == [(home_league().user_id, home_league().key)]


def test_cache_for(other):
    assert api.cache_for(home_league()) is api.cache_for(home_league())
    c = api.cache_for(other)
    assert c is api.cache_for(other) and c is not api.cache_for(home_league())


def test_same_key_two_leagues_never_cross(other):
    n = {"home": 0, "other": 0}

    def build(who):
        def fn():
            n[who] += 1
            return {"who": who}
        return fn
    for _ in range(2):
        assert api._serve("roster", build("home"), ref=home_league())["who"] == "home"
        assert api._serve("roster", build("other"), ref=other)["who"] == "other"
    assert n == {"home": 1, "other": 1}


def test_other_league_pages_persist_under_its_key(other):
    api._serve("roster", lambda: {"who": "other"}, ref=other)
    assert [p["key"] for p in db.pages(ref=other)] == ["roster"] and db.pages(ref=home_league()) == []
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
    api._serve("roster", build("home"), ref=home_league())
    api._serve("roster", build("other"), ref=other)
    assert len(journal.sync_transactions(NS(), ref=other)) == 1
    api.cache_for(other).join()  # the move dropped other's page and rewarmed it behind; home's stood
    assert built == {"home": 1, "other": 2}
    assert db.unsent_notices(ref=home_league()) == [] and [n["key"] for n in db.unsent_notices(ref=other)] == ["move:1788937502756-3-FA ADDED-77"]



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
    assert {ref for _, ref in two} == {home_league()}


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


def test_other_league_scores_live_on_its_own_points_and_feed(client, two, monkeypatch):
    monkeypatch.setattr(api.live.STATE, "leagues", {HOME_KEY: ({11: 9.5}, 3), "espn:999": ({11: 4.0}, 3)})
    monkeypatch.setattr(api, "_plays", lambda data, limit=None, *, ref: [{"delta": 2.0 if ref.key != HOME_KEY else 1.0}])
    theirs = client.get("/week?league=espn:999").json()
    assert theirs["me"]["lineup"][0]["actual"] == 4.0 and theirs["plays"] == [{"delta": 2.0}]
    mine = client.get("/week").json()
    assert mine["me"]["lineup"][0]["actual"] == 9.5 and mine["plays"] == [{"delta": 1.0}]
    assert client.get("/plays?league=espn:999").json()["plays"] == [{"delta": 2.0}]
    assert client.get("/team/3?league=espn:999").json()["side"]["lineup"][0]["actual"] == 4.0
    assert client.get("/team/3").json()["side"]["lineup"][0]["actual"] == 9.5
    assert client.get("/roster?league=espn:999").json()["cards"][0]["actual"] == 4.0


def test_other_league_points_wait_for_its_own_week(client, two, monkeypatch):
    """Before its first week the league's page week (its first) is past the NFL week the feed scores: no overlay."""
    monkeypatch.setattr(api.live.STATE, "leagues", {"espn:999": ({11: 4.0}, 2)})
    assert client.get("/week?league=espn:999").json()["me"]["lineup"][0]["actual"] == 1.0


def test_play_store_is_the_runner_lane_of_the_league(monkeypatch, other):
    home_store, their_store = object(), object()
    lanes = {HOME_KEY: NS(week=3, store=home_store, roster={1: {}}), other.key: NS(week=3, store=their_store, roster={1: {}})}
    runner = NS(lanes=list(lanes.values()), lane=lambda key: lanes.get(key))
    monkeypatch.setattr(api.app.state, "runner", runner, raising=False)
    assert api._play_store(3, ref=home_league()) is home_store and api._play_store(3, ref=home_league()) is home_store
    assert api._play_store(3, ref=other) is their_store
    assert isinstance(api._play_store(2, ref=other), api.plays_mod.Store) and api._play_store(2, ref=other).ref == other


def test_other_league_roster_attaches_its_own_outlooks(client, two, other, monkeypatch):
    loaded = []
    monkeypatch.setattr(api.outlook, "load", lambda week, kind="roster", *, ref: loaded.append(ref) or {})
    monkeypatch.setattr(api.outlook, "attach", lambda cards, entries, writing: [{**c, "outlook": "x"} for c in cards])
    body = client.get("/roster?league=espn:999").json()
    assert body["cards"][0]["outlook"] == "x" and body["outlook_run"]["running"] is False
    assert loaded == [other]
    client.get("/roster")
    assert loaded[-1] == home_league()


def test_watch_per_league(client, two, other):
    assert client.post("/watch/11?league=espn:999", json={"watched": True}).json() == {"watched": True, "ids": [11]}
    assert db.watched_ids(ref=other) == [11] and db.watched_ids(ref=home_league()) == []


def test_presets_are_every_leagues(client, other):
    """The Players table's views are the same in every league: one made in one league shows in the other."""
    pid = client.post("/players/presets?league=espn:999", json={"name": "Mine", "pos": "ALL", "fa": True, "cols": ["snap_pct"], "filters": [],
                                                                   "sort": {"id": "snap_pct", "desc": True}}).json()["id"]
    assert pid in {p["id"] for p in client.get("/players/presets?league=espn:999").json()["presets"]}
    assert pid in {p["id"] for p in client.get("/players/presets").json()["presets"]}
    client.post("/players/presets/default", json={"id": pid})
    assert client.get("/players/presets?league=espn:999").json()["default"] == pid


def test_decision_other_league_404(client, other):
    i = db.add_decision({"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app", "title": "T",
                         "decision": "d", "rationale": None, "player_ids": []}, ref=home_league())
    assert client.post(f"/decisions/{i}?league=espn:999", json={"outcome": "ok"}).status_code == 404
    assert client.post(f"/decisions/{i}", json={"outcome": "ok"}).status_code == 200


# outlooks for every league (4.7)
def test_outlook_routes_follow_the_open_league(client, two, other, monkeypatch):
    es = api.espn_season
    monkeypatch.setattr(es, "free_agent_pool", lambda *a, ref=None, **k: {"week": 3, "positions": {"RB": [
        {"player_id": 7, "name": "A", "league": ref.key}]}, "watched": []})
    started = []
    monkeypatch.setattr(api.outlook, "start_one", lambda row, week, *a, ref=None, **k: started.append((row.get("league"), ref)))
    monkeypatch.setattr(api.outlook, "waiver_entry", lambda row, week, *, ref=None: {"league": ref.key})
    assert client.post("/waivers/outlook/7?league=espn:999").status_code == 202
    assert started == [("espn:999", other)]
    assert client.get("/waivers/outlook/7?league=espn:999").json() == {"league": "espn:999"}
    monkeypatch.setattr(api.outlook, "roster_entry", lambda card, week, *, ref=None: {"league": ref.key})
    assert client.get("/outlook/11?league=espn:999").json() == {"league": "espn:999"}
    assert client.post("/outlook/11?league=espn:999").status_code == 202 and started[-1] == (None, other)


def test_outlook_refresh_runs_one_league_for_a_user_and_every_league_for_a_job(client, two, other, monkeypatch, request):
    runs = []

    def start_leagues(jobs, run=None):
        runs.append([(ref.key, build()) for ref, build in jobs])
    monkeypatch.setattr(api.outlook, "start_leagues", start_leagues)
    # the Roster page's button names its league; with none (or an empty one), the user's default
    assert client.post("/outlook/refresh?league=espn:999").status_code == 202
    assert [k for k, _ in runs[-1]] == ["espn:999"]
    for bare in ("", "?league="):
        assert client.post(f"/outlook/refresh{bare}").status_code == 202
        assert [k for k, _ in runs[-1]] == [HOME_KEY]
    request.getfixturevalue("as_job")  # the hourly timer: every league, each one's cards read in the run
    assert client.post("/outlook/refresh").status_code == 202
    [(home_key, (cards, week)), (other_key, (their_cards, _))] = runs[-1]
    assert (home_key, other_key, week) == (HOME_KEY, "espn:999", 3)


def test_refresh_other_league(monkeypatch, client, other):
    synced = []
    monkeypatch.setattr(api, "sync_all", lambda *, ref=None: synced.append(ref) or {"transactions": True})
    built = {"home": 0, "other": 0}

    def build(who):
        def fn():
            built[who] += 1
            return {"who": who}
        return fn
    api._serve("roster", build("home"), ref=home_league())
    api._serve("roster", build("other"), ref=other)
    assert client.post("/refresh", params={"league": "espn:999"}).status_code == 200
    assert synced == [other]
    api._serve("roster", build("home"), ref=home_league())
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


def test_transactions_sync_with_no_league_syncs_every_league(monkeypatch, client, other, as_job):
    synced = []
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda ref=None, **k: ref.key)
    monkeypatch.setattr(api, "load", lambda ref=None, **k: {})
    monkeypatch.setattr(api, "sync_results", lambda lg, settings, *, ref=None: None)

    def sync(lg, *, ref=None):
        synced.append(lg)
        if lg == "espn:999":
            raise RuntimeError("espn down")
        return [{}, {}]
    monkeypatch.setattr(api, "sync_transactions", sync)
    r = client.post("/transactions/sync")
    assert synced == [home_league().key, "espn:999"], "every league ran, the failing one too"
    assert r.status_code == 502 and r.json()["detail"]["leagues"] == {HOME_KEY: {"new": 2}, "espn:999": {"error": "espn down"}}


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
    lock = api._lock(name, home_league())
    assert lock is api._lock(name, home_league()) and api._lock(name, other) is api._lock(name, other)
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


def test_each_league_has_its_own_locks(other):
    assert api._lock("lineup", home_league()) is api._lock("lineup", home_league())
    assert api._lock("lineup", home_league()) is not api._lock("lineup", other)
    assert api._lock("lineup", home_league()) is not api._lock("submit", home_league())


def test_write_decisions_land_under_their_league(monkeypatch, client, other, writes):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    r = client.post("/roster/sit/11?league=espn:999", json={"sat": True})
    assert r.status_code == 200 and r.json()["decision"]["id"]
    assert [d["title"].startswith("[fake]") for d in db.decisions(ref=other)] == [True]
    assert db.decisions(ref=home_league()) == []
    assert db.sat_ids(8, ref=other) == {11} and db.sat_ids(8, ref=home_league()) == set()


def test_roster_state_reads_its_league_fake_slots(monkeypatch, other):
    """FFL_WRITES=0: a fake move under other puts other's card in the new slot; home's card keeps ESPN's slot."""
    monkeypatch.setenv("FFL_WRITES", "0")
    espn_write.move_lineup([(1, "QB", "BE")], 3, ref=other)
    assert espn_write.fake_slots(ref=other) == {1: "BE"} and espn_write.fake_slots(ref=home_league()) == {}
    rows = [{"player_id": 1, "slot": "QB"}]
    flags = {1: {"slot": "QB", "order": 0, "locked": False}}
    slot = lambda fake: api.espn_season.with_espn_slots(rows, flags, fake)[0]["slot"]  # noqa: E731
    assert slot(espn_write.fake_slots(ref=other)) == "BE" and slot(espn_write.fake_slots(ref=home_league())) == "QB"


def test_lineup_week_check_reads_its_league(client, other, writes):
    writes.period.update({home_league().key: 7, other.key: 8})
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
    fake, home = espn_write._fake(other), espn_write._fake(home_league())
    assert fake.adds and fake.claims
    assert not home.adds and not home.claims
    assert len(db.decisions(ref=other)) == 2 and all(d["title"].startswith("[fake]") for d in db.decisions(ref=other))
    assert db.decisions(ref=home_league()) == []
    plan = db.claim_plan(6, ref=other)
    assert plan["groups"] == [{"targets": [101], "cuts": [2], "open": 0}] and len(plan["submits"]) == 1
    assert db.claim_plan(6, ref=home_league()) is None


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
    writes.period[home_league().key] = 7  # home's page is on 8
    r = client.post("/lineup", json=SWAP)
    assert r.status_code == 409 and r.json()["detail"] == {"error": "ESPN's week changed — reload"}


# the league switcher (ffl-soc.4.13): every registered league with its format, and the pages it opens on
def test_leagues_lists_every_league_home_first(monkeypatch, client, other):
    two, one = {"starters": {"QB": 2, "RB": 2}, "team_count": 10}, {"starters": {"QB": 1, "RB": 2}, "team_count": 12}
    monkeypatch.setattr(api.espn_season, "load", lambda ref=None: one if ref is not None and ref.key == other.key else two)
    rows = client.get("/leagues").json()
    assert [r["key"] for r in rows] == [home_league().key, "espn:999"]
    home, them = rows
    assert home["home"] is True and "pages" not in home, "every league opens every page (4.9)"
    assert (home["provider"], home["format"]) == ("ESPN", "2QB · 10 teams")
    assert them == {"key": "espn:999", "name": "other", "provider": "ESPN", "format": "1QB · 12 teams", "home": False}


def test_leagues_survives_unreadable_settings(monkeypatch, client, other):
    def broken(ref=None):
        raise SystemExit("no settings")
    monkeypatch.setattr(api.espn_season, "load", broken)
    rows = client.get("/leagues").json()
    assert [r["format"] for r in rows] == [None, None]


def test_live_leagues_are_every_league_whose_settings_read(monkeypatch, other):
    monkeypatch.setattr(api, "load", lambda ref: {"scoring": [], "who": ref.key})
    [(home, _, _), (ref, settings, fn)] = api.live_leagues(leagues.all_leagues())
    assert home == home_league() and ref == other and settings["who"] == "espn:999" and callable(fn)

    def missing(ref):
        raise SystemExit("no settings")
    monkeypatch.setattr(api, "load", missing)
    assert api.live_leagues(leagues.all_leagues()) == []



def test_notify_tick_does_each_leagues_steps_once_and_the_nfl_plays_once(client, other, monkeypatch):
    calls = []
    monkeypatch.setattr(api.notify, "tick", lambda: {"leagues": {HOME_KEY: {"moves": 0}, "espn:999": {"moves": 1}}})
    for name in ("rollover_pass", "correction_pass", "watch_pass"):
        monkeypatch.setattr(api, name, lambda *, ref, name=name: calls.append((name, ref)) or {name: True})
    monkeypatch.setattr(api, "pregame_pull", lambda now=None, *, ref: calls.append(("pregame_pull", ref)) or [])
    monkeypatch.setattr(api, "final_plays", lambda: calls.append(("final_plays", None)) or {})
    out = client.post("/notify/tick").json()
    for step in ("rollover_pass", "correction_pass", "watch_pass", "pregame_pull"):
        assert [r for n, r in calls if n == step] == [home_league(), other], step
    assert [n for n, _ in calls].count("final_plays") == 1
    assert out["leagues"][HOME_KEY]["corrections"] == {"correction_pass": True} and out["final_plays"] == {}
    assert out["leagues"][HOME_KEY]["rollover"] == {"rollover_pass": True}
    assert out["leagues"]["espn:999"] == {"moves": 1, "rollover": {"rollover_pass": True},
                                          "corrections": {"correction_pass": True},
                                          "watch": {"0": {"watch_pass": True}}, "pregame": []}


def test_espn_rolling_its_week_drops_that_leagues_pages_and_rebuilds_them(client, other, monkeypatch):
    """Tuesday's rollover: a page expired by its TTL is served once from last week while it rebuilds, so the first
    open of every page showed the old week. The tick sees ESPN's period move on and drops the league's pages (never
    served again) and the cached Leagues, then rebuilds them behind; the first read only takes a baseline."""
    refreshed = []
    monkeypatch.setattr(api.espn_season, "refresh_league", lambda: refreshed.append(1))
    fake = api.espn_write._fake(other)
    monkeypatch.setattr(fake, "period", 7)
    for c in (api.cache_for(other), api.cache_for(home_league())):
        c.get_or_build("roster", lambda: {"week": 7})
    assert api.rollover_pass(ref=other) == {"week": 7}, "the first read is a baseline"
    assert api.rollover_pass(ref=other) == {"week": 7}
    assert api.cache_for(other).has("roster") and refreshed == []
    monkeypatch.setattr(fake, "period", 8)
    api.cache_for(other)._d["roster"].fn = lambda: {"week": 8}
    assert api.rollover_pass(ref=other) == {"week": 8, "rolled_from": 7}
    api.cache_for(other).join()
    assert api.cache_for(other).peek("roster") == {"week": 8}, "rebuilt behind, never the old week again"
    assert api.cache_for(home_league()).peek("roster") == {"week": 7}, "the other league's week did not move"
    assert refreshed == [1]
    assert api.rollover_pass(ref=other) == {"week": 8}


def test_league_tick_steps_read_and_write_their_league(client, other, monkeypatch):
    lgs = []
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda ref=None, **k: lgs.append(ref) or f"LG:{ref.key if ref else None}")
    monkeypatch.setattr(api.espn_season, "correct_boxes", lambda lg, now=None, *, ref=None: {
        "weeks": {}, "changed": [5], "plays": [{"week": 5, "player_id": 1, "at": "t", "total": 1.0, "team_id": 3}],
        "lg": lg, "ref": ref.key})
    api.cache_for(other).get_or_build("league:5", lambda: {"k": 1})
    api.cache_for(home_league()).get_or_build("league:5", lambda: {"k": 1})
    out = api.correction_pass(ref=other)
    assert out["lg"] == "LG:espn:999" and out["ref"] == "espn:999"
    assert [r["player_id"] for r in db.plays_rows(5, ref=other)] == [1] and db.plays_rows(5, ref=home_league()) == []
    assert api.cache_for(other)._d["league:5"].expires == 0.0 and api.cache_for(home_league())._d["league:5"].expires > 0.0
    db.put_watch(11, False, ref=other)
    assert api.watch_pass(ref=other) == {"skipped": "none watched"}


def test_notify_switches_follow_the_open_league(client, other):
    assert client.post("/notify/prefs?league=espn:999", json={"news": False}).json()["news"] is False
    assert client.get("/notify/config?league=espn:999").json()["prefs"]["news"] is False
    assert client.get("/notify/config").json()["prefs"]["news"] is True
    assert client.post("/notify/prefs?league=espn:999", json={"quiet": ["bad"]}).status_code == 422



def test_news_marks_whose_players_in_the_open_league(client, other, monkeypatch):
    asked = []
    monkeypatch.setattr(api.pipeline, "owners", lambda *, ref: asked.append(ref) or ({}, 3))
    monkeypatch.setattr(api.news_players, "fetch_index", lambda: api.news_players.Index())
    client.get("/news?league=espn:999")
    client.get("/news")
    assert asked == [other, home_league()]


def _limits(league: str, **limits):
    import sqlalchemy as sa
    with db._write() as c:
        for pos, n in limits.items():
            c.execute(db._upsert(db.league_caps, [{"league": league, "pos": pos.replace("DST", "D/ST"), "provider_limit": n}],
                                 ["league", "pos"], ["provider_limit"]))
    assert sa  # the import is the table's


def test_caps_are_the_leagues_own_and_never_above_its_limit(other):
    """Roster caps are the draft tools' (`ffl.leagues caps`), set from the CLI only."""
    from ffl import strategy
    _limits("espn:999", QB=4, RB=8)
    home_before = db.caps_rows(ref=home_league())
    leagues.set_cap(other, "QB", 2)
    assert leagues.caps(other)["QB"] == 2 and strategy.caps(other)["QB"] == 2, "the draft tools read the same rows"
    with pytest.raises(SystemExit, match="at most 4"):
        leagues.set_cap(other, "QB", 5)
    assert db.caps_rows(ref=home_league()) == home_before, "home's rows are its own"


def test_no_caps_route(client, other):
    """The web app has no draft tool, so neither caps nor a strategy reach it (ffl-srn)."""
    assert client.get("/caps?league=espn:999").status_code == 404
    assert client.post("/caps?league=espn:999", json={"pos": "QB", "my_cap": 2}).status_code == 404



def test_rename_the_open_league(client, other):
    r = client.post("/leagues/name?league=espn:999", json={"name": "Indy"})
    assert r.status_code == 200 and r.json() == {"key": "espn:999", "name": "Indy"}
    assert [x["name"] for x in client.get("/leagues").json()] == [home_league().name, "Indy"]
    assert client.post("/leagues/name?league=espn:999", json={"name": " "}).status_code == 422
    home_name = home_league().name
    assert client.post("/leagues/name", json={"name": "Mine"}).json()["name"] == "Mine"
    assert home_league().name == "Mine" != home_name
