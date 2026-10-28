"""The watchlist's API: the star, the Watch page, and the notify tick's hourly pass. The pool, Sleeper's chart and
ESPN's injury feed are stubbed; no network."""
import pytest
from fastapi.testclient import TestClient

from ffl import api, db
from tests.home import HOME_KEY, home_league

ME, THEM = 7, 3


def _row(pid, name, pos="RB", team="DET", owner=None, delta=1.0, add=5.0):
    club = {"team_id": owner, "name": f"T{owner}", "abbrev": f"T{owner}"} if owner else None
    return {"player_id": pid, "name": name, "pos": pos, "team": team, "owner": club, "mine": owner == ME,
            "add": None if owner == ME else add, "delta_roster": None if owner == ME else delta, "form": None}


@pytest.fixture
def pool(monkeypatch):
    """What `free_agent_pool`, Sleeper's chart and ESPN's injury feed answer; a test edits it before the build."""
    state = {"watched": [], "builds": 0, "chart": {}, "injuries": {}, "owners": None}

    def fake_pool(week=None, **k):
        state["builds"] += 1
        return {"week": 7, "ros_week": 7, "positions": {"RB": []}, "rostered": {"RB": []},
                "roster": [{"player_id": 90, "name": "Mine", "pos": "RB", "ros_adj": 30.0, "slot": "BE"}],
                "watched": [dict(r) for r in state["watched"]]}

    monkeypatch.setattr(api.espn_season, "free_agent_pool", fake_pool)
    monkeypatch.setattr(api.news, "fetch_depth", lambda: state["chart"])
    monkeypatch.setattr(api.espn_season, "fetch_injuries", lambda: state["injuries"])

    def fake_owners(**k):
        """The fresh owners read: the pool rows' owners unless a test sets its own (or an exception to raise)."""
        if isinstance(state["owners"], BaseException):
            raise state["owners"]
        if state["owners"] is not None:
            return state["owners"], ME
        return {r["player_id"]: r["owner"] for r in state["watched"] if r["owner"]}, ME

    monkeypatch.setattr(api.pipeline, "owners", fake_owners)
    api.cache_for(home_league()).clear()
    yield state
    api.cache_for(home_league()).clear()


@pytest.fixture
def client(pool):
    return TestClient(api.app)


def _rerun():
    """The pass again within the hour: its gate cleared, its baselines kept, the pool rebuilt."""
    st = db.get_league_state("watch_state", {}, ref=home_league())
    st.pop("last_pass", None)
    db.put_league_state("watch_state", st, ref=home_league())
    api.cache_for(home_league()).clear()
    return api.watch_pass(ref=home_league())


def test_watch_toggle_and_ids(client, pool):
    assert client.get("/watch/ids").json() == {"ids": []}
    client.get("/watch")  # the pool is cached, without him
    built = pool["builds"]
    r = client.post("/watch/5", json={"watched": True})
    assert r.status_code == 200 and r.json() == {"watched": True, "ids": [5]}
    assert client.get("/watch/ids").json() == {"ids": [5]}
    # newly watched and missing from the cached pool: it rebuilds behind the next request
    pool["watched"] = [_row(5, "Five")]
    client.get("/watch")
    api.cache_for(home_league()).join()
    assert pool["builds"] == built + 1
    # already in the cached pool: nothing expires
    client.post("/watch/5", json={"watched": True})
    client.get("/watch")
    api.cache_for(home_league()).join()
    assert pool["builds"] == built + 1
    assert client.post("/watch/5", json={"watched": False}).json() == {"watched": False, "ids": []}
    assert client.post("/watch/5", json={"watched": "maybe"}).status_code == 422


def test_watch_get_groups_and_drops_mine(client, pool):
    for pid in (5, 6, 8, 9):
        db.put_watch(pid, True, ref=home_league())
    db.put_watch(9, False, ref=home_league())  # unwatched since the pool was cached: left out though the payload has him
    pool["watched"] = [_row(5, "Free"), _row(6, "Theirs", owner=THEM), _row(8, "Now mine", owner=ME),
                       _row(9, "Gone"), _row(10, "Quiet", add=2.0)]
    db.put_watch(10, True, ref=home_league())
    db.add_watch_signals([{"key": "usage:5:6", "player_id": 5, "kind": "usage", "text": "snaps 40→70%",
                           "at": api.notify.now()}], ref=home_league())
    body = client.get("/watch").json()
    assert body["week"] == 7 and body["ros_week"] == 7 and body["stale_since"] is None
    g = body["groups"]
    assert [r["player_id"] for r in g["moving"]] == [5]
    assert [r["player_id"] for r in g["quiet"]] == [10]
    assert [r["player_id"] for r in g["elsewhere"]] == [6]
    assert next(c for c in g["moving"][0]["chips"] if c["kind"] == "usage")["lit"] is True
    assert db.watched_ids(ref=home_league()) == [5, 6, 10]  # the one now on my team is no longer watched


def _ahead_setup(pool, owner=None):
    db.put_watch(5, True, ref=home_league())
    pool["watched"] = [_row(5, "Backup Back", owner=owner)]
    pool["chart"] = {"DET": [{"name": "Lead Back", "pos": "RB", "slot": "RB", "order": 1, "injury": None},
                             {"name": "Backup Back", "pos": "RB", "slot": "RB", "order": 2, "injury": None}]}
    pool["injuries"] = {11: {"team": "DET", "pos": "RB", "name": "Lead Back", "status": "Out", "note": None,
                             "return_date": None, "body_part": None}}


def test_pass_pushes_free_agent_once(pool):
    _ahead_setup(pool)
    assert api.watch_pass(ref=home_league()) == {"signals": 1, "queued": 1}
    assert db.watch_signals("", ref=home_league())[0]["text"] == "Lead Back Out"
    assert _rerun() == {"signals": 0, "queued": 0}
    rows = [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch"]
    assert [n["key"] for n in rows] == [f"watch:5:{api.notify.now()[:10]}"]
    assert rows[0]["title"] == "Backup Back" and rows[0]["url"] == "/players?watch=1"


def test_pass_never_pushes_rostered_elsewhere(pool):
    _ahead_setup(pool, owner=THEM)
    assert api.watch_pass(ref=home_league()) == {"signals": 1, "queued": 0}
    assert [s["kind"] for s in db.watch_signals("", ref=home_league())] == ["ahead"]
    assert not [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch"]
    assert db.get_league_state("watch_state", ref=home_league())["owner"] == {"5": THEM}


def test_pass_available_pushes_on_drop(pool):
    db.put_watch(5, True, ref=home_league())
    db.put_league_state("watch_state", {"owner": {"5": THEM}, "bench": {"5": 1.0}, "above": {}}, ref=home_league())
    pool["watched"] = [_row(5, "Dropped")]
    assert api.watch_pass(ref=home_league()) == {"signals": 1, "queued": 1}
    n = next(n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch")
    assert n["body"] == "available: dropped"
    assert db.get_league_state("watch_state", ref=home_league())["owner"] == {"5": None}


def test_pass_bench_crossing(pool):
    db.put_watch(5, True, ref=home_league())
    db.put_league_state("watch_state", {"owner": {}, "bench": {"5": -2.0}, "above": {}}, ref=home_league())
    pool["watched"] = [_row(5, "Riser", delta=3.0)]
    assert api.watch_pass(ref=home_league()) == {"signals": 1, "queued": 1}
    assert db.watch_signals("", ref=home_league())[0]["text"] == "passes Mine on your bench"


def test_pass_hourly_gate(pool):
    _ahead_setup(pool)
    api.watch_pass(ref=home_league())
    assert api.watch_pass(ref=home_league()) == {"skipped": "hourly"}
    assert _rerun() == {"signals": 0, "queued": 0}


def test_pass_drops_mine(pool):
    db.put_watch(8, True, ref=home_league())
    pool["watched"] = [_row(8, "Now mine", owner=ME)]
    assert api.watch_pass(ref=home_league()) == {"signals": 0, "queued": 0}
    assert db.watched_ids(ref=home_league()) == []


def test_notify_tick_reports_a_failed_pass(pool, monkeypatch):
    monkeypatch.setattr(api.notify, "tick", lambda: {})
    monkeypatch.setattr(api, "correction_pass", lambda **k: {})
    monkeypatch.setattr(api, "final_plays", lambda: {})
    monkeypatch.setattr(api, "pregame_pull", lambda **k: [])
    monkeypatch.setattr(api, "watch_pass", lambda **k: (_ for _ in ()).throw(RuntimeError("pool down")))
    assert TestClient(api.app).post("/notify/tick").json()["leagues"][HOME_KEY]["watch"] == {"0": {"error": "pool down"}}, "each member's pass its own line"


def test_unwatch_clears_state_no_false_available_on_rewatch(client, pool):
    db.put_watch(5, True, ref=home_league())
    db.put_league_state("watch_state", {"owner": {"5": THEM}, "bench": {"5": 1.0}, "above": {"5": ["lead back"]}}, ref=home_league())
    client.post("/watch/5", json={"watched": False})
    st = db.get_league_state("watch_state", ref=home_league())
    assert "5" not in st["owner"] and "5" not in st["bench"] and "5" not in st["above"]
    assert _rerun() == {"skipped": "none watched"}
    client.post("/watch/5", json={"watched": True})
    pool["watched"] = [_row(5, "Back again")]
    assert _rerun() == {"signals": 0, "queued": 0}
    assert not [s for s in db.watch_signals("", ref=home_league()) if s["kind"] == "available"]
    assert not [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch"]


def test_pass_prunes_state_to_watched_ids_not_priced_rows(pool):
    for pid in (5, 6):
        db.put_watch(pid, True, ref=home_league())
    db.put_watch(9, False, ref=home_league())
    db.put_league_state("watch_state", {"owner": {"5": THEM, "6": THEM, "9": THEM}, "bench": {"9": 1.0}, "above": {"9": []}}, ref=home_league())
    pool["watched"] = [_row(5, "Priced", owner=THEM)]  # 6 is watched but this pool has no row for him
    api.watch_pass(ref=home_league())
    st = db.get_league_state("watch_state", ref=home_league())
    assert st["owner"] == {"5": THEM, "6": THEM}  # 6's baseline survives a read that missed him; 9's is gone
    assert st["bench"] == {"5": 1.0} and st["above"] == {"5": []}


def test_pass_gates_push_on_fresh_owners(pool):
    _ahead_setup(pool)
    pool["owners"] = {5: {"team_id": THEM, "name": "T3", "abbrev": "T3"}}  # added since the pool was built
    assert api.watch_pass(ref=home_league()) == {"signals": 1, "queued": 0}
    assert [s["kind"] for s in db.watch_signals("", ref=home_league())] == ["ahead"]
    assert not [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch"]


def test_pass_fresh_owners_mine_unwatches(pool):
    _ahead_setup(pool)
    pool["owners"] = {5: {"team_id": ME, "name": "T7", "abbrev": "T7"}}
    assert api.watch_pass(ref=home_league())["queued"] == 0
    assert db.watched_ids(ref=home_league()) == []
    assert not [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch"]


@pytest.mark.parametrize("err", [RuntimeError("espn down"), SystemExit("no league")])
def test_pass_queues_nothing_when_owners_read_fails(pool, err):
    _ahead_setup(pool)
    pool["owners"] = err
    assert api.watch_pass(ref=home_league()) == {"signals": 1, "queued": 0, "owners": "espn down" if isinstance(err, RuntimeError) else "no league"}
    assert [s["kind"] for s in db.watch_signals("", ref=home_league())] == ["ahead"]
    assert not [n for n in db.unsent_notices(ref=home_league()) if n["kind"] == "watch"]


def test_pass_skips_build_when_none_watched(pool):
    assert api.watch_pass(ref=home_league()) == {"skipped": "none watched"}
    assert pool["builds"] == 0


def test_watch_get_returns_ids_after_unwatching_mine(client, pool):
    for pid in (5, 8):
        db.put_watch(pid, True, ref=home_league())
    pool["watched"] = [_row(5, "Free"), _row(8, "Now mine", owner=ME)]
    assert client.get("/watch").json()["ids"] == [5]
