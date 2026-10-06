"""API glue: caching, stale fallback, validation. espn_season is stubbed; no network."""
import time
from types import SimpleNamespace

import pytest
import requests
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
    monkeypatch.setattr(api.espn_season, "league_state", lambda week=None: {"week": week or 3, "standings": [], "scoreboard": []})
    monkeypatch.setattr(api.espn_season, "team_state", lambda team_id: {
        "week": 3, "live": False, "mine": team_id == 7, "club": {"team_id": team_id, "rank": 2},
        "side": {"team_id": team_id, "lineup": [{"player_id": 1, "actual": 0.0, "team": "DET"}], "bench": [], "ir": []}})
    monkeypatch.setattr(api.espn_season, "roster_state",
                        lambda week=None: {"week": 3, "live": False, "cards": [
                            {"player_id": 1, "name": "A", "pos": "RB", "team": "DET", "bye": False, "injury": "ACTIVE",
                             "proj": 12.0, "spread": 1.0, "game_played": 0, "opponent": "GB", "kickoff": None,
                             "return_week": None, "injury_note": None, "call": "START", "close_call": False,
                             "reason": "starts at RB: no bench alternative", "group": "starter", "gap": None}]})
    monkeypatch.setattr(api.outlook.news, "gather", lambda cards, week: {c["player_id"]: {"depth": None, "teammates": [], "rotowire": [], "last_news": None} for c in cards})
    monkeypatch.setattr(api.outlook, "OUTLOOK_DIR", tmp_path / "outlook")
    # start()'s thread looks LAST up by name at call time, so a fresh dict per test keeps runs apart
    monkeypatch.setattr(api.outlook, "LAST",
                        {"at": None, "generated": 0, "skipped": 0, "failed": 0, "error": None,
                         "last_failure": None, "pending": None})
    monkeypatch.setattr(api, "sync_all", lambda: {"transactions": 0})
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


def test_refresh_keeps_the_stale_fallback(client, monkeypatch):
    """expire_all, not clear: refreshing while ESPN is down must not blank the page."""
    client.get("/week/3")
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    client.post("/refresh")
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["stale_since"] is not None


def test_refresh_502s_when_espn_is_down(client, monkeypatch):
    monkeypatch.setattr(api, "sync_all", lambda: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.post("/refresh")
    assert r.status_code == 502 and r.json()["detail"] == {"source": "espn", "error": "espn down"}


def test_sync_all_rebuilds_the_league(monkeypatch):
    """The League caches current_week and the records; sync_results would otherwise rewrite
    weekly.csv truncated to whatever week the process booted in."""
    hits = {"refresh": 0, "fresh": 0}
    monkeypatch.setattr(api.espn_season, "refresh_league",
                        lambda: hits.__setitem__("refresh", hits["refresh"] + 1))
    monkeypatch.setattr(api.espn_season, "fresh_league",
                        lambda: (hits.__setitem__("fresh", hits["fresh"] + 1), "LG")[1])
    monkeypatch.setattr(api, "load", lambda: {})
    monkeypatch.setattr(api, "sync_transactions", lambda lg: None)
    monkeypatch.setattr(api, "sync_results", lambda lg, s: None)
    assert api.sync_all() == {"transactions": True, "results": True}
    assert hits == {"refresh": 1, "fresh": 1}


def test_stale_payload_served_when_source_fails(client, monkeypatch):
    client.get("/week/3")
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    api.cache.expire_all()
    client.get("/week/3")  # answered from the old payload; the rebuild behind it fails
    api.cache.join()
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["stale_since"] is not None


def test_expired_entry_is_served_at_once_and_rebuilt_behind(client, monkeypatch):
    """An expired payload is answered immediately; the rebuild runs behind the request."""
    import threading
    gate = threading.Event()
    seen = {"n": 0}

    def slow_week(week=None):
        seen["n"] += 1
        if seen["n"] > 1:
            gate.wait(5)
        return {"week": 3, "live": False, "me": {"team": f"me{seen['n']}"}, "opp": None}

    monkeypatch.setattr(api.espn_season, "week_state", slow_week)
    assert client.get("/week/3").json()["me"]["team"] == "me1"
    api.cache.expire_all()
    t0 = time.time()
    r = client.get("/week/3").json()
    assert time.time() - t0 < 1, "the request did not wait on the rebuild"
    assert r["me"]["team"] == "me1" and r["stale_since"] is None, "expired is not 'source down'"
    assert client.get("/week/3").json()["me"]["team"] == "me1"
    assert seen["n"] == 2, "one rebuild in flight per key, however many requests arrive"
    gate.set()
    api.cache.join()
    assert client.get("/week/3").json()["me"]["team"] == "me2"


def test_failed_background_rebuild_keeps_the_old_payload_and_flags_it(client, monkeypatch):
    client.get("/week/3")
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    api.cache.expire_all()
    assert client.get("/week/3").json()["stale_since"] is None, "not known to be down yet"
    api.cache.join()
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["me"]["team"] == "me" and r.json()["stale_since"] is not None
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None: {"week": 3, "live": False, "me": {"team": "back"}, "opp": None})
    client.get("/week/3")  # still flagged and expired: this get starts the retry
    api.cache.join()
    assert client.get("/week/3").json()["stale_since"] is None, "a good build clears the flag"


def test_refresh_rebuilds_the_hot_keys_inline(client):
    """The Refresh button is the one place the user asks to wait for fresh data."""
    client.get("/week/3")
    client.post("/refresh")
    assert client.calls["week"] == 2, "rebuilt during the refresh, not lazily on the next get"
    client.get("/week/3")
    assert client.calls["week"] == 2


def test_startup_warms_the_page_caches(client):
    with TestClient(api.app):
        api.cache.join()
    assert client.calls["week"] == 1
    assert all(api.cache.has(k) for k in ("week:current", "roster", "league:current", "waivers"))


def test_logo_serves_bytes_and_404s_when_missing(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "team_logo", lambda tid: (b"\x89PNG", "image/png"))
    r = client.get("/logo/7")
    assert r.status_code == 200 and r.content == b"\x89PNG" and r.headers["content-type"] == "image/png"
    monkeypatch.setattr(api.espn_season, "team_logo", lambda tid: (_ for _ in ()).throw(LookupError("no logo")))
    r = client.get("/logo/8")
    assert r.status_code == 404


def test_502_when_nothing_cached(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "league_state", lambda week=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.get("/league")
    assert r.status_code == 502 and r.json()["detail"]["source"] == "espn"


def test_waivers_requires_known_pos(client):
    r = client.get("/waivers?pos=RB")
    assert r.status_code == 200 and r.json()["ros_source"] == "season", "the stub carries no ROS sources"
    assert r.json()["ros_week"] == 3 and r.json()["week_over"] is False, "a pool without the fields reads as ESPN's week"
    assert client.get("/waivers?pos=XX").status_code == 422


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_transactions_are_served_through_the_cache(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api.espn_season, "transactions_state",
                        lambda: calls.append(1) or {"me": 7, "current_week": 5, "moves": [], "teams": []})
    assert client.get("/transactions").json() == {"me": 7, "current_week": 5, "moves": [], "teams": [], "stale_since": None}
    client.get("/transactions")
    assert len(calls) == 1


def test_player_transactions_filter_the_cached_moves(client, monkeypatch):
    side = {"team_id": 6, "team": "T6", "abbrev": "T", "logo": "", "added": [{"player_id": 101, "name": "A"}], "dropped": []}
    move = {"id": "5-6", "ms": 5, "at": "2026-09-10T03:05", "kind": "waiver", "week": 1, "sides": [side]}
    monkeypatch.setattr(api.espn_season, "transactions_state",
                        lambda: {"me": 7, "current_week": 5, "moves": [move], "teams": []})
    monkeypatch.setattr(api.espn_season, "draft_picks", lambda: [
        {"overall": 24, "round": 3, "pick": 4, "team_id": 6, "team": "T6", "player_id": 101, "at": None}])
    got = client.get("/player/101/transactions").json()["history"]
    assert [h["kind"] for h in got] == ["waiver", "draft"], "the draft is the oldest entry"
    assert client.get("/player/999/transactions").json()["history"] == []


def test_player_transactions_survive_a_failed_draft_read(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "transactions_state", lambda: {"me": 7, "current_week": 5, "moves": [], "teams": []})

    def boom():
        raise RuntimeError("espn down")

    monkeypatch.setattr(api.espn_season, "draft_picks", boom)
    assert client.get("/player/101/transactions").json()["history"] == []


def test_team_overlays_live_points(client, monkeypatch):
    monkeypatch.setattr(api.live.STATE, "points", {1: 14.5})
    monkeypatch.setattr(api.live.STATE, "week", 3)
    body = client.get("/team/4").json()
    assert body["club"]["rank"] == 2 and body["mine"] is False and body["stale_since"] is None
    assert body["side"]["lineup"][0]["actual"] == 14.5 and body["side"]["actual_total"] == 14.5


def test_refresh_expires_the_injury_cache_but_keeps_the_last_good_data(client, monkeypatch):
    """_INJ is a last-good store: Refresh must bust its TTL, not throw the data away."""
    inj = api.espn_season
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        raise requests.ConnectionError("espn down")

    monkeypatch.setattr(inj.requests, "get", fake_get)
    inj._INJ[:] = [time.time() + inj.CACHE_S, {1: {"return_date": None, "note": "n", "body_part": None}}]
    good = inj._INJ[1]
    client.post("/refresh")
    assert inj._INJ and inj._INJ[0] == 0.0, "expired, not cleared"
    assert inj.fetch_injuries() == good, "ESPN down after a Refresh: the stale data still serves"
    assert calls, "the expiry did force a refetch attempt"


def test_roster_attaches_the_cached_outlook_on_every_get(client):
    r = client.get("/roster")
    assert r.status_code == 200
    body = r.json()
    assert body["cards"][0]["outlook"] is None and body["cards"][0]["outlook_stale"] is False
    assert body["outlook_run"] == {"at": None, "generated": 0, "skipped": 0, "failed": 0,
                                   "error": None, "last_failure": None, "pending": None, "running": False}
    take = {"label": "Take", "text": "He starts.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    api.outlook.save(3, {1: {"hash": "h", "card_hash": api.outlook.card_hash(body["cards"][0]), "bullets": [take], "generated_at": "t"}})
    assert client.get("/roster").json()["cards"][0]["outlook"] == [take], "no TTL wait for new bullets"


def test_outlook_refresh_starts_a_run_and_answers_409_while_busy(client, monkeypatch):
    import threading
    gate = threading.Event()
    take = {"label": "Take", "text": "He starts.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    monkeypatch.setattr(api.outlook, "run_claude", lambda prompt: (gate.wait(5), [take])[1])
    r = client.post("/outlook/refresh")
    assert r.status_code == 202 and r.json() == {"started": True, "pending": None}
    assert client.post("/outlook/refresh").status_code == 409
    assert client.get("/roster").json()["outlook_run"]["running"] is True
    gate.set()
    for _ in range(50):
        if not api.outlook.running():
            break
        time.sleep(0.05)
    assert client.get("/roster").json()["cards"][0]["outlook"] == [take]
    assert client.get("/roster").json()["outlook_run"]["generated"] == 1


def test_outlook_refresh_reports_a_source_failure_as_502(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "roster_state",
                        lambda week=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.post("/outlook/refresh")
    assert r.status_code == 502 and r.json()["detail"]["source"] == "espn"


def _fa_pool(monkeypatch):
    row = {"player_id": 7, "name": "B", "pos": "RB", "team": "MIA", "bye": False, "injury": "ACTIVE",
           "opponent": "BUF", "kickoff": None, "return_week": None, "injury_note": None, "add": 9.0}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None: {"week": 3, "positions": {"RB": [row], "WR": []}})
    monkeypatch.setattr(api.outlook, "_WRITING", {})
    api.cache.clear()
    return row


def test_waiver_outlook_is_written_on_a_post_and_read_on_a_get(client, monkeypatch):
    import threading
    _fa_pool(monkeypatch)
    gate = threading.Event()
    take = {"label": "Take", "text": "A stash.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    monkeypatch.setattr(api.outlook, "run_claude", lambda prompt: (gate.wait(5), [take])[1])
    assert client.get("/waivers?pos=RB").json()["rows"][0]["has_outlook"] is False
    assert client.get("/waivers/outlook/7").json() == {"bullets": None, "at": None, "stale": False, "running": False, "error": None}
    assert client.post("/waivers/outlook/7").status_code == 202
    assert client.post("/waivers/outlook/7").status_code == 409
    assert client.get("/waivers/outlook/7").json()["running"] is True
    gate.set()
    for _ in range(50):
        if not client.get("/waivers/outlook/7").json()["running"]:
            break
        time.sleep(0.05)
    assert client.get("/waivers/outlook/7").json()["bullets"] == [take]
    assert client.get("/waivers?pos=RB").json()["rows"][0]["has_outlook"] is True


def test_waiver_outlook_for_a_player_off_the_wire_is_404(client, monkeypatch):
    _fa_pool(monkeypatch)
    assert client.get("/waivers/outlook/1").status_code == 404
    assert client.post("/waivers/outlook/1").status_code == 404


def test_live_points_from_another_week_never_overlay_the_current_page(client, monkeypatch):
    """After Monday night the feed still holds last week's totals until the next kickoff; the
    week payload rolls over first, and last week's points must not land on it."""
    st = api.live.State()
    st.update({1: 24.7}, {"DET": {"status": "post"}}, {1: {"3": 200.0}}, week=2)
    monkeypatch.setattr(api.live, "STATE", st)
    assert api._live_points(3) == {} and api._live_games(3) == {} and api._live_stats(3) == {}
    st.update({1: 24.7}, {"DET": {"status": "post"}}, {1: {"3": 200.0}}, week=3)
    assert api._live_points(3) == {1: 24.7}


def _store(rows):
    return SimpleNamespace(latest=lambda n=None, teams=None: [r for r in rows if teams is None or r["team_id"] in teams][:n])


def test_week_plays_come_only_from_the_runner_scoring_that_week(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None: {"week": week or 3, "live": False, "me": {"team_id": 7}, "opp": {"team_id": 3}})
    store = _store([{"delta": 6.0, "team_id": 7}])
    monkeypatch.setattr(api.app.state, "runner", SimpleNamespace(week=2, store=store), raising=False)
    assert client.get("/week").json()["plays"] == []
    api.cache.clear()
    monkeypatch.setattr(api.app.state, "runner", SimpleNamespace(week=3, store=store), raising=False)
    assert client.get("/week").json()["plays"] == [{"delta": 6.0, "team_id": 7, "side": "me"}]


def test_every_board_carries_its_own_games_plays_sided_from_its_left(client, monkeypatch):
    """`/week/{n}?team=` opens any scoreboard matchup; it caches under its own key, so my week's
    board is never served for someone else's game, and each board's feed is its two teams' rows."""
    asked = []

    def fake_week(week=None, team=None):
        asked.append(team)
        left, right = (7, 3) if team is None else (team, 5)
        return {"week": week or 3, "live": False, "me": {"team": f"t{left}", "team_id": left, "abbrev": "L"},
                "opp": {"team": "x", "team_id": right, "abbrev": "R"}, "mine": team is None}

    monkeypatch.setattr(api.espn_season, "week_state", fake_week)
    store = _store([{"delta": 6.0, "team_id": 7}, {"delta": 2.0, "team_id": 5}, {"delta": 1.0, "team_id": 4}])
    monkeypatch.setattr(api.app.state, "runner", SimpleNamespace(week=3, store=store), raising=False)
    assert [(p["delta"], p["side"]) for p in client.get("/week/3").json()["plays"]] == [(6.0, "me")]
    r = client.get("/week/3?team=4").json()
    assert r["me"]["team"] == "t4" and [(p["delta"], p["side"]) for p in r["plays"]] == [(2.0, "opp"), (1.0, "me")]
    client.get("/week/3?team=4")
    assert asked == [None, 4]
    every = client.get("/plays?team=4").json()
    assert every["mine"] is False and every["me"] == {"team_id": 4, "team": "t4", "abbrev": "L"}
    assert [p["delta"] for p in every["plays"]] == [2.0, 1.0]
    assert [p["delta"] for p in client.get("/plays").json()["plays"]] == [6.0]


def test_roster_cards_never_carry_last_weeks_live_overlay(client, monkeypatch):
    """The Roster page is gated on its own week like /week: after the rollover the feed still
    holds last week's points, final scores and stat lines, and none of them may reach this
    week's cards."""
    st = api.live.State()
    st.update({1: 24.7}, {"DET": {"status": "post"}}, {1: {"3": 200.0}}, week=2)
    monkeypatch.setattr(api.live, "STATE", st)
    card = client.get("/roster").json()["cards"][0]
    assert card.get("actual") in (None, 0) and "game" not in card and not card.get("stat_line")
    st.update({1: 24.7}, {"DET": {"status": "post"}}, {1: {"3": 200.0}}, week=3)
    card = client.get("/roster").json()["cards"][0]
    assert card["game"]["status"] == "post"


def test_card_outlook_is_rewritten_on_a_post_and_polled_on_a_get(client, monkeypatch):
    import threading
    gate = threading.Event()
    take = {"label": "Take", "text": "He starts.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    monkeypatch.setattr(api.outlook, "run_claude", lambda prompt: (gate.wait(5), [take])[1])
    monkeypatch.setattr(api.outlook, "_WRITING", {})
    assert client.post("/outlook/1").status_code == 202
    assert client.post("/outlook/1").status_code == 409
    assert client.get("/outlook/1").json()["outlook_running"] is True
    assert client.get("/roster").json()["cards"][0]["outlook_running"] is True
    gate.set()
    for _ in range(50):
        if not client.get("/outlook/1").json()["outlook_running"]:
            break
        time.sleep(0.05)
    assert client.get("/outlook/1").json()["outlook"] == [take]
    assert client.get("/roster").json()["cards"][0]["outlook"] == [take]
    assert client.post("/outlook/99").status_code == 404 and client.get("/outlook/99").status_code == 404


PLAYER_LOG = {"name": "X", "pos": "QB", "team": "BUF", "season": 2026, "current_week": 4,
              "seasons": [{"season": 2026, "weeks": [], "totals": None}]}


def test_player_route(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "player_card", lambda pid: {**PLAYER_LOG, "player_id": pid})
    r = client.get("/player/7")
    assert r.status_code == 200 and r.json()["player_id"] == 7 and r.json()["stale_since"] is None


def test_player_502(client, monkeypatch):
    def boom(pid):
        raise RuntimeError("espn down")
    monkeypatch.setattr(api.espn_season, "player_card", boom)
    assert client.get("/player/8").status_code == 502


def test_player_live_week(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "player_card", lambda pid: {**PLAYER_LOG, "player_id": pid})
    monkeypatch.setattr(api.live.STATE, "stats", {9: {"0": 10, "1": 7, "3": 80}})
    monkeypatch.setattr(api.live.STATE, "points", {9: 3.2})
    monkeypatch.setattr(api.live.STATE, "week", 4)
    monkeypatch.setattr(api.live.STATE, "games", {"BUF": {"status": "in"}})
    monkeypatch.setattr(api, "_scoring", lambda: [{"stat_id": 3, "label": "Passing yards", "points": 0.04}])
    weeks = client.get("/player/9").json()["seasons"][0]["weeks"]
    assert [(w["week"], w["status"], w["pts"]) for w in weeks] == [(4, "live", 3.2)]


def test_waivers_search_spans_every_position(client, monkeypatch):
    rb = _fa_pool(monkeypatch)
    wr = {**rb, "player_id": 8, "name": "Bo Nix", "pos": "WR", "team": "DEN", "add": 12.0}
    qb = {**rb, "player_id": 9, "name": "Cam Ward", "pos": "QB", "team": "TEN", "add": 3.0}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None: {"week": 3, "positions": {"RB": [rb], "WR": [wr], "QB": [qb]}})
    api.cache.clear()
    body = client.get("/waivers?q=b").json()
    assert [r["player_id"] for r in body["rows"]] == [8, 7], "every pool, by add, best first"
    assert body["pos"] is None and body["q"] == "b" and all("has_outlook" in r for r in body["rows"])
    assert [r["player_id"] for r in client.get("/waivers?q=ten&pos=RB").json()["rows"]] == [9], "q ignores pos"
    assert client.get("/waivers").status_code == 422, "no pos and no query is still an error"
    assert client.get("/waivers?q=%20&pos=RB").json()["pos"] == "RB", "a blank query is no query"


@pytest.fixture
def claims_env(client, monkeypatch, tmp_path):
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    pool = {"WR": [{"player_id": 101, "name": "Addison", "status": "WAIVERS"}, {"player_id": 102, "name": "Mims", "status": "WAIVERS"}],
            "D/ST": [{"player_id": -16020, "name": "Vikings D/ST", "status": "WAIVERS"}]}
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: {"week": 6, "roster": roster, "positions": pool})
    # reads the current pool dict on every call, so a test can flip a status between load and Submit
    monkeypatch.setattr(api.espn_season, "pool_status", lambda: {r["player_id"]: {"status": r["status"], "waiver_date": None} for rows in pool.values() for r in rows})
    monkeypatch.setattr(api, "CLAIMS_DIR", tmp_path / "waivers")
    monkeypatch.setattr(api, "DECISIONS", tmp_path / "DECISIONS.md")
    monkeypatch.setattr(api, "_roster_size", lambda: 17)
    api.espn_write._FAKE.clear()
    api.espn_write._FAKE_ADDS.clear()
    api.espn_write._FAIL_ADDS.clear()
    api.cache.clear()
    client.pool = pool
    return client


def test_claims_empty_week(claims_env):
    r = claims_env.get("/claims").json()
    assert r["week"] == 6 and r["groups"] == [] and r["open_spots"] == 1 and r["last_submit"] is None


def test_plan_roundtrip_dst(claims_env):
    g = {"targets": [-16020, 101], "cuts": [1], "open": 1}
    r = claims_env.post("/claims", json={"groups": [g]}).json()
    assert r["groups"] == [g] and r["errors"] == [] and r["claims"][0] == {"group": 0, "add": -16020, "drop": None}
    assert claims_env.get("/claims").json()["groups"] == [g]


def test_submit_happy_path_and_decision(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["match"] and len(r["sent"]) == 4 and r["now"] == []
    g = claims_env.get("/claims").json()
    assert g["edited_since_submit"] is False and all(p["from_app"] for p in g["pending"])
    assert "Waiver claims (from the app)" in api.DECISIONS.read_text()


def test_submit_free_agent_is_instant(claims_env):
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"          # Mims cleared waivers
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["now"] == [{"group": 0, "add": 102, "drop": 1}] and [(c["add"], c["drop"]) for c in r["sent"]] == [(101, 2)]
    assert api.espn_write._FAKE_ADDS == [{"add": 102, "drop": 1}]


def test_submit_uses_fresh_status(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.get("/claims")                                  # pool cached with 101 on WAIVERS
    claims_env.pool["WR"][0]["status"] = "FREEAGENT"           # the Wednesday run cleared him
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["now"] == [{"group": 0, "add": 101, "drop": 1}] and r["sent"] == []


def test_submit_rejects_invalid(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [555], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 422 and "not on your roster" in str(r.json())


def test_submit_rejects_undroppable(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [3], "open": 0}]})
    api.cache.clear()
    pool_fn = api.espn_season.free_agent_pool

    def with_undroppable(week=None):
        d = pool_fn(week)
        d["roster"] = [{**p, "droppable": p["player_id"] != 3} for p in d["roster"]]
        return d

    monkeypatch.setattr(api.espn_season, "free_agent_pool", with_undroppable)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 422 and "undroppable" in str(r.json())


def test_submit_expired_login(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})

    def boom():
        raise api.espn_write.WriteError(401, "expired")

    monkeypatch.setattr(api.espn_write, "pending_claims", boom)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 502 and "login expired" in r.json()["detail"]["error"]


def test_new_week_starts_empty(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: {"week": 7, "roster": [], "positions": {}})
    api.cache.clear()
    r = claims_env.get("/claims").json()
    assert r["week"] == 7 and r["groups"] == [] and r["last_submit"] is None


def test_edit_after_submit_flags(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [1], "open": 0}]})
    assert claims_env.get("/claims").json()["edited_since_submit"] is True
