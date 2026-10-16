"""API glue: caching, stale fallback, validation. espn_season is stubbed; no network."""
import json
import threading
import time
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from ffl import api, db


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
    api.espn_season._SEASON_CACHE[3] = (time.time(), {})
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


def test_transactions_sync_answers_the_new_count(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: "LG")
    monkeypatch.setattr(api, "load", lambda: {})
    monkeypatch.setattr(api, "sync_transactions", lambda lg: [{}, {}])
    results = []
    monkeypatch.setattr(api, "sync_results", lambda lg, settings: results.append(lg))
    r = client.post("/transactions/sync")
    assert r.status_code == 200 and r.json() == {"new": 2}
    assert results == ["LG"], "the hourly sync keeps the results table too"
    monkeypatch.setattr(api, "sync_transactions", lambda lg: [])
    assert client.post("/transactions/sync").json() == {"new": 0}


def test_transactions_sync_502s_when_espn_is_down(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: "LG")
    monkeypatch.setattr(api, "sync_transactions", lambda lg: (_ for _ in ()).throw(RuntimeError("espn down")))
    assert client.post("/transactions/sync").status_code == 502


def test_sync_all_rebuilds_the_league(monkeypatch):
    """The League caches current_week and the records; sync_results would otherwise rewrite
    the results table truncated to whatever week the process booted in."""
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


def test_players_requires_known_pos(client):
    r = client.get("/players?pos=RB")
    assert r.status_code == 200 and r.json()["ros_source"] == "season", "the stub carries no ROS sources"
    assert r.json()["ros_week"] == 3 and r.json()["week_over"] is False, "a pool without the fields reads as ESPN's week"
    assert client.get("/players?pos=XX").status_code == 422


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
    assert client.get("/players?pos=RB").json()["rows"][0]["has_outlook"] is False
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
    assert client.get("/players?pos=RB").json()["rows"][0]["has_outlook"] is True


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


def test_plays_page_opens_the_week_it_was_linked_from(client, monkeypatch):
    """A finished week's board links its whole feed with `week`: the page reads that week's stored rows, not
    the current week's, and leads back to that week's board (`current` false); my current week stays `/`."""
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None, team=None: {
        "week": week or 6, "live": False, "me": {"team": "t", "team_id": team or 7, "abbrev": "L"},
        "opp": {"team": "x", "team_id": 5, "abbrev": "R"}, "mine": team in (None, 7)})
    monkeypatch.setattr(api.app.state, "runner", SimpleNamespace(week=6, store=_store([{"delta": 6.0, "team_id": 7}])),
                        raising=False)
    monkeypatch.setattr(api.db, "plays_rows", lambda week: [{"delta": 2.0, "team_id": 7, "week": week}])
    past = client.get("/plays?week=5").json()
    assert past["week"] == 5 and past["current"] is False and [p["delta"] for p in past["plays"]] == [2.0]
    other = client.get("/plays?week=5&team=4").json()
    assert other["week"] == 5 and other["me"]["team_id"] == 4
    now = client.get("/plays?week=6").json()
    assert now["week"] == 6 and now["current"] is True and [p["delta"] for p in now["plays"]] == [6.0]
    assert client.get("/plays").json()["current"] is True


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


def test_players_search_spans_every_position(client, monkeypatch):
    rb = _fa_pool(monkeypatch)
    wr = {**rb, "player_id": 8, "name": "Bo Nix", "pos": "WR", "team": "DEN", "add": 12.0}
    qb = {**rb, "player_id": 9, "name": "Cam Ward", "pos": "QB", "team": "TEN", "add": 3.0}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None: {"week": 3, "positions": {"RB": [rb], "WR": [wr], "QB": [qb]}})
    api.cache.clear()
    body = client.get("/players?q=b").json()
    assert [r["player_id"] for r in body["rows"]] == [8, 7], "every pool, by add, best first"
    assert body["pos"] is None and body["q"] == "b" and all("has_outlook" in r for r in body["rows"])
    assert [r["player_id"] for r in client.get("/players?q=ten&pos=RB").json()["rows"]] == [9], "q ignores pos"
    assert client.get("/players").status_code == 422, "no pos and no query is still an error"
    assert client.get("/players?q=%20&pos=RB").json()["pos"] == "RB", "a blank query is no query"


@pytest.fixture
def claims_env(client, monkeypatch):
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    pool = {"WR": [{"player_id": 101, "name": "Addison", "status": "WAIVERS"}, {"player_id": 102, "name": "Mims", "status": "WAIVERS"}],
            "D/ST": [{"player_id": -16020, "name": "Vikings D/ST", "status": "WAIVERS"}]}
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: {"week": 6, "roster": roster, "positions": pool})
    # reads the current pool dict on every call, so a test can flip a status between load and Submit
    monkeypatch.setattr(api.espn_season, "pool_status", lambda: {r["player_id"]: {"status": r["status"], "waiver_date": None} for rows in pool.values() for r in rows})
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


def test_submit_happy_path_and_decision(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["match"] and len(r["sent"]) == 4 and r["now"] == []
    g = claims_env.get("/claims").json()
    assert g["edited_since_submit"] is False and all(p["from_app"] for p in g["pending"])
    assert _titles() == ["Week 6 waiver claims"]
    assert r["decisions"] == [{"id": db.decisions()[0]["id"], "needs_rationale": True}]


def test_a_resubmit_updates_the_weeks_claims_row_and_keeps_its_rationale(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    first = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"][0]
    db.update_decision(first["id"], {"rationale": ["best_ros"]})
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [1], "open": 0}]})
    again = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"]
    assert again == [{"id": first["id"], "needs_rationale": False}]
    assert len(db.decisions()) == 1 and "Mims" in db.decisions()[0]["decision"]


def test_a_dev_api_submit_records_no_decision(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    assert claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"] == []
    assert db.decisions() == []


def test_a_test_server_records_a_fake_claim_only_when_asked(claims_env, monkeypatch):
    # FFL_WRITES=0 (conftest): the claims go to the in-memory fake, the page says so, and nothing is recorded
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    assert claims_env.get("/claims").json()["writes"] is False
    assert claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"] == []
    assert db.decisions() == [] and api.espn_write._FAKE, "the fake took the claim; the decisions table did not"
    # FFL_RECORD_FAKE=1 (a worktree test server, own DB): recorded, marked fake, as /lineup and sit do
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]}).json()
    rows = {row["id"]: row for row in db.decisions()}
    assert [rows[d["id"]]["title"] for d in r["decisions"]] == ["[fake] Week 6 waiver claims", "[fake] Week 6 add: Mims, drop R1"]
    assert all(row["facts"]["fake"] is True for row in rows.values())


def test_claims_say_when_writes_are_real(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    assert claims_env.get("/claims").json()["writes"] is True


def test_an_empty_plan_withdraws_the_claims_sent_from_here(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    outside = api.espn_write.submit_claim(-16020, 3, 6)                 # filed on ESPN's site, not here
    claims_env.post("/claims", json={"groups": []})
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["sent"] == [] and r["match"] and [c["id"] for c in r["pending"]] == [outside], "ours gone, the outside one kept"
    g = claims_env.get("/claims").json()
    assert g["last_submit"]["claims"] == [] and g["last_submit"]["ids"] == [] and g["errors"] == []
    assert [c["from_app"] for c in g["pending"]] == [False]
    r = claims_env.post("/claims/submit", json={"replace_outside": True}).json()
    assert r["pending"] == [] and api.espn_write._FAKE == [], "the box ticked: the outside claim goes too"


def test_an_undesignated_ir_player_blocks_the_submit(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    api.cache.clear()
    pool_fn = api.espn_season.free_agent_pool

    def with_bad_ir(week=None):
        d = pool_fn(week)
        d["roster"] = d["roster"] + [{"player_id": 99, "slot": "IR", "name": "Stuck", "pos": "RB", "droppable": True, "locked": False, "injury": "ACTIVE"}]
        return d

    monkeypatch.setattr(api.espn_season, "free_agent_pool", with_bad_ir)
    g = claims_env.get("/claims").json()
    assert g["errors"] == [{"group": None, "player_id": 99, "reason": api.claims_mod.IR_BLOCK}]
    assert g["players"]["99"]["name"] == "Stuck", "the panel can name him"
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["player_id"] == 99
    assert api.espn_write._FAKE == [], "nothing reached the writer"
    # a fake lineup move (FFL_WRITES=0) took him off IR: the claims read the fake's slots, as the Roster page does
    monkeypatch.setattr(api.espn_write, "_FAKE_SLOTS", {99: "BE"})
    assert claims_env.get("/claims").json()["errors"] == []
    assert claims_env.post("/claims/submit", json={"replace_outside": False}).status_code == 200


def test_a_lineup_move_rebuilds_the_claims_roster(lineup_env, monkeypatch):
    builds = {"n": 0}

    def pool(week=None):
        builds["n"] += 1
        return {"week": 3, "roster": [], "positions": {"WR": []}}

    monkeypatch.setattr(api.espn_season, "free_agent_pool", pool)
    monkeypatch.setattr(api, "_roster_size", lambda: 17)
    lineup_env.get("/claims")
    lineup_env.get("/claims")
    assert builds["n"] == 1
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    lineup_env.get("/claims")
    assert builds["n"] == 2, "the move off IR the panel asked for must clear the block on the next read"


def test_waiver_run_retires_the_submitted_plan(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    api.espn_write._FAKE.clear()                               # the run processed the claims
    monkeypatch.setattr(api.espn_write, "_FAKE_LAST_RUN", "2999-01-01T00:00:00")
    g = claims_env.get("/claims").json()
    assert g["groups"] == [] and g["last_submit"] is None and g["errors"] == []
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [2], "open": 0}]})
    saved = db.claim_plan(6)
    assert saved["submits"] == [] and saved["retired"][0]["groups"][0]["targets"] == [101]
    assert claims_env.get("/claims").json()["groups"][0]["targets"] == [102], "a plan saved after the run stays"


def test_a_plan_after_the_waiver_run_is_a_new_claims_decision(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    ran = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"][0]
    db.update_decision(ran["id"], {"rationale": ["best_ros"]})
    api.espn_write._FAKE.clear()                               # the run processed the claims
    monkeypatch.setattr(api.espn_write, "_FAKE_LAST_RUN", "2999-01-01T00:00:00")
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [2], "open": 0}]})
    fresh = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"]
    assert fresh[0]["id"] != ran["id"] and fresh[0]["needs_rationale"] is True
    rows = {r["id"]: r for r in db.decisions()}
    assert "Addison" in rows[ran["id"]]["decision"] and rows[ran["id"]]["rationale"] == ["best_ros"]
    assert "Mims" in rows[fresh[0]["id"]]["decision"] and rows[fresh[0]["id"]]["facts"]["gen"] == 1


def test_submit_free_agent_is_instant(claims_env):
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"          # Mims cleared waivers
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]}).json()
    assert r["now"] == [{"group": 0, "add": 102, "drop": 1}] and [(c["add"], c["drop"]) for c in r["sent"]] == [(101, 2)]
    assert api.espn_write._FAKE_ADDS == [{"add": 102, "drop": 1}]


def test_instant_add_rebuilds_only_the_roster_pages_inline(claims_env):
    built = {"week:current": 0, "player:9": 0}

    def builder(key):
        def fn():
            built[key] += 1
            return {}
        return fn
    for key in built:
        api.cache.get_or_build(key, builder(key))
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert built == {"week:current": 2, "player:9": 1}         # the player card waits for its next request
    assert api.cache._d["player:9"].expires == 0.0


def test_submit_uses_fresh_status(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.get("/claims")                                  # pool cached with 101 on WAIVERS
    claims_env.pool["WR"][0]["status"] = "FREEAGENT"           # the Wednesday run cleared him
    shown = claims_env.get("/claims?fresh=1").json()["now"]    # the Submit dialog's read on open
    assert shown == [{"group": 0, "add": 101, "drop": 1}]
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": shown}).json()
    assert r["now"] == shown and r["sent"] == []


def test_fresh_claims_falls_back_to_cached_status(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})

    def boom():
        raise RuntimeError("ESPN down")

    monkeypatch.setattr(api.espn_season, "pool_status", boom)
    r = claims_env.get("/claims?fresh=1")
    assert r.status_code == 200 and r.json()["status"] == {"101": "WAIVERS", "102": "WAIVERS", "-16020": "WAIVERS"}


def test_submit_status_flip_since_preview_is_409(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    shown = claims_env.get("/claims?fresh=1").json()["now"]
    assert shown == []                                         # previewed: all claims
    claims_env.pool["WR"][0]["status"] = "FREEAGENT"           # cleared between the preview and Send
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": shown})
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "statuses changed — review again", "now": [{"group": 0, "add": 101, "drop": None}]}
    assert api.espn_write._FAKE == [] and api.espn_write._FAKE_ADDS == []


def test_instant_adds_gate_off_rejects_free_agent_targets(claims_env, monkeypatch):
    monkeypatch.setenv("FFL_INSTANT_ADDS", "0")
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    assert api.claims_mod.INSTANT_OFF in [e["reason"] for e in claims_env.get("/claims").json()["errors"]]
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 422 and "FFL_INSTANT_ADDS" in str(r.json())
    assert api.espn_write._FAKE == [] and api.espn_write._FAKE_ADDS == []
    monkeypatch.delenv("FFL_INSTANT_ADDS")                     # unset is on
    assert claims_env.get("/claims").json()["errors"] == []


def test_submit_espn_week_changed_is_409(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "_FAKE_PERIOD", 7)     # the pool is cached at week 6
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 409 and r.json()["detail"] == {"error": "ESPN's week changed — reload"}
    assert api.espn_write._FAKE == [] and api.espn_write._FAKE_ADDS == []
    assert not api.cache.has("waivers")                        # the reload builds the new week's pool


def test_submit_writes_carry_espn_period(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "_FAKE_PERIOD", 6)
    weeks = []
    real_submit, real_add = api.espn_write.submit_claim, api.espn_write.add_free_agent
    monkeypatch.setattr(api.espn_write, "submit_claim", lambda a, d, wk: weeks.append(wk) or real_submit(a, d, wk))
    monkeypatch.setattr(api.espn_write, "add_free_agent", lambda a, d, wk: weeks.append(wk) or real_add(a, d, wk))
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 200 and weeks == [6, 6]


def test_landed_add_rebuilds_the_pool_before_returning(claims_env, monkeypatch):
    calls = {"n": 0}
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]

    def pool_fn(week=None):
        calls["n"] += 1
        r = roster
        if api.espn_write._FAKE_ADDS:                          # ESPN now has the add: Mims on, R1 off
            r = [p for p in roster if p["player_id"] != 1] + [{**roster[0], "player_id": 102, "name": "Mims"}]
        return {"week": 6, "roster": r, "positions": claims_env.pool}

    monkeypatch.setattr(api.espn_season, "free_agent_pool", pool_fn)
    api.cache.clear()
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    before = calls["n"]
    claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert calls["n"] == before + 1                            # rebuilt inline, not left to a background thread
    ids = {p["player_id"] for p in claims_env.get("/players?pos=WR").json()["roster"]}
    assert 102 in ids and 1 not in ids
    assert calls["n"] == before + 1                            # served fresh: no rebuild behind the GET


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

    monkeypatch.setattr(api.espn_write, "pending_state", boom)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 502 and "login expired" in r.json()["detail"]["error"]


def test_unreachable_espn_is_not_a_login_expired(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})

    def boom():
        raise api.espn_write.WriteError(0, "ReadTimeout: read timed out")

    monkeypatch.setattr(api.espn_write, "pending_state", boom)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 502 and r.json()["detail"]["error"] == api.espn_write.UNREACHABLE
    assert claims_env.get("/claims").json()["pending_error"] == api.espn_write.UNREACHABLE
    monkeypatch.setattr(api.espn_write, "pending_state", lambda: (_ for _ in ()).throw(api.espn_write.WriteError(403, "x")))
    assert claims_env.get("/claims").json()["pending_error"] == api.espn_write.EXPIRED


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


def test_submit_readback_failure_still_journals(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})

    def down():   # the read-back only: the pre-submit read is pending_state
        raise api.espn_write.WriteError(503, "down")

    monkeypatch.setattr(api.espn_write, "pending_claims", down)
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 200 and r.json()["error"] and r.json()["match"] is False
    plan = db.claim_plan(6)
    assert len(plan["submits"]) == 1 and plan["submits"][0]["now"] == [{"group": 0, "add": 102, "drop": 1}]
    assert db.decisions(), "the landed add is recorded"


def test_failed_cancel_keeps_app_claim_tracked(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    old = [p["id"] for p in claims_env.get("/claims").json()["pending"]]
    real, calls = api.espn_write.cancel_claim, {"n": 0}

    def flaky(i, wk):
        calls["n"] += 1
        if calls["n"] == 2:
            raise api.espn_write.WriteError(500, "boom")
        return real(i, wk)

    monkeypatch.setattr(api.espn_write, "cancel_claim", flaky)
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["error"] and r["sent"] == []
    plan = db.claim_plan(6)
    assert set(plan["submits"][-1]["ids"]) == set(old) - {old[0]}   # the first was cancelled, the rest still held
    assert old[1] in plan["submits"][-1]["ids"]
    pend = claims_env.get("/claims").json()["pending"]
    assert pend and all(p["from_app"] for p in pend)


def test_save_during_submit_is_409(claims_env):
    assert api._SUBMIT.acquire(blocking=False)
    try:
        r = claims_env.post("/claims", json={"groups": []})
    finally:
        api._SUBMIT.release()
    assert r.status_code == 409
    assert claims_env.post("/claims", json={"groups": []}).status_code == 200   # released


def test_instant_add_is_consumed_from_the_plan(claims_env, monkeypatch):
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False}
              for i in range(2, 17)] + [{"player_id": 102, "slot": "BE", "name": "Mims", "pos": "WR", "droppable": True, "locked": False}]
    pool = {"WR": [claims_env.pool["WR"][0]]}   # Mims left the pool, R1 left the roster
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: {"week": 6, "roster": roster, "positions": pool})
    api.cache.clear()
    r = claims_env.get("/claims").json()
    assert r["groups"] == [{"targets": [101], "cuts": [2], "open": 0}]
    assert r["errors"] == [] and r["edited_since_submit"] is False


def test_on_espn_checks_espn_not_the_record(claims_env):
    assert claims_env.get("/claims").json()["on_espn"] is False             # never sent
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    plan = db.claim_plan(6)
    assert plan["submits"][-1]["match"] is True
    assert claims_env.get("/claims").json()["on_espn"] is True
    api.espn_write._FAKE.pop(1)                                             # one cancelled on ESPN's site
    g = claims_env.get("/claims").json()
    assert g["on_espn"] is False and g["edited_since_submit"] is False


def test_on_espn_ignores_kept_outside_claims(claims_env):
    api.espn_write._FAKE.append({"id": "outside-1", "add": 201, "drop": 5})  # filed on ESPN directly
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    assert claims_env.get("/claims").json()["on_espn"] is True


def test_target_on_my_roster_is_an_error(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [5], "cuts": [1], "open": 0}]})
    assert "already on your roster" in [e["reason"] for e in claims_env.get("/claims").json()["errors"]]


def test_save_plan_failure_keeps_the_old_plan(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    good = db.claim_plan(6)

    def crash(*a, **k):
        raise db.DBError("disk full")

    monkeypatch.setattr(db, "_upsert", crash)
    with pytest.raises(db.DBError):
        api._save_plan({**good, "groups": []})
    assert db.claim_plan(6) == good


def _league_pool(monkeypatch, positions_rb=None):
    base = {"pos": "RB", "bye": False, "injury": "ACTIVE", "opponent": "BUF", "kickoff": None,
            "return_week": None, "injury_note": None}
    fa = {**base, "player_id": 7, "name": "B", "team": "MIA", "add": 9.0, "owner": None, "mine": False}
    theirs = {**base, "player_id": 20, "name": "Bijan Robinson", "team": "ATL", "add": 14.0, "mine": False,
              "owner": {"team_id": 2, "name": "Them", "abbrev": "THM"}}
    mine = {**base, "player_id": 30, "name": "Kenny Gainwell", "team": "TB", "add": None, "delta_roster": None,
            "delta_pool": None, "mine": True, "owner": {"team_id": 7, "name": "Mine", "abbrev": "ME"}}
    pool = {"week": 3, "positions": {"RB": positions_rb if positions_rb is not None else [fa], "WR": []},
            "rostered": {"RB": [theirs, mine], "WR": []}}
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None: pool)
    monkeypatch.setattr(api.outlook, "_WRITING", {})
    api.cache.clear()
    return pool


def test_players_free_agents_by_default_everyone_with_fa_0(client, monkeypatch):
    _league_pool(monkeypatch)
    body = client.get("/players?pos=RB").json()
    assert body["fa"] is True and [r["player_id"] for r in body["rows"]] == [7]
    body = client.get("/players?pos=RB&fa=0").json()
    assert [r["player_id"] for r in body["rows"]] == [20, 7, 30], "add desc, my own (None) last"
    assert body["rows"][0]["owner"]["abbrev"] == "THM" and body["rows"][1]["owner"] is None
    assert all(r["has_outlook"] is False for r in body["rows"] if r["owner"])
    assert client.get("/players?pos=RB&fa=maybe").status_code == 422, "yes/no/1/0 parse; anything else is a 422"


def test_players_search_spans_everyone_whatever_the_toggle(client, monkeypatch):
    _league_pool(monkeypatch)
    assert [r["player_id"] for r in client.get("/players?q=robinson&fa=1").json()["rows"]] == [20]
    me = client.get("/players?q=gainwell").json()["rows"]
    assert [(r["player_id"], r["mine"], r["add"]) for r in me] == [(30, True, None)]


def test_players_one_row_when_espn_lists_a_rostered_player_as_free(client, monkeypatch):
    dup = {"player_id": 20, "name": "Bijan Robinson", "pos": "RB", "team": "ATL", "add": 40.0, "owner": None, "mine": False}
    _league_pool(monkeypatch, positions_rb=[dup])
    rows = client.get("/players?pos=RB&fa=0").json()["rows"]
    assert [r["player_id"] for r in rows].count(20) == 1 and rows[0]["owner"]["team_id"] == 2


def test_players_pool_cached_before_rostered_existed(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None: {"week": 3, "positions": {"RB": [{"player_id": 7, "name": "B", "pos": "RB", "team": "MIA", "add": 1.0}]}})
    api.cache.clear()
    assert [r["player_id"] for r in client.get("/players?pos=RB&fa=0").json()["rows"]] == [7]


def test_claims_never_see_a_rostered_player(monkeypatch):
    pool = _league_pool(monkeypatch)
    assert set(api._pool_rows(pool)) == {7}


@pytest.fixture
def lineup_env(client, monkeypatch):
    cards = [{"player_id": 3, "name": "Bench", "pos": "RB", "slot": "BE", "call": "START", "gap": 1.4, "group": "bench", "locked": False},
             {"player_id": 2, "name": "Flex", "pos": "WR", "slot": "RB/WR/TE", "call": "BENCH", "gap": -1.4, "group": "starter", "locked": False}]
    state = {"slots": {3: "BE", 2: "RB/WR/TE"}, "locked": set(), "period": 3, "builds": 0, "flag_reads": 0}

    def roster_state(week=None, flags=None):
        state["builds"] += 1
        state["readback_flags"] = flags
        return {"week": 3, "live": False, "empty_slots": [], "writes": False,
                "cards": [{**c, "slot": state["slots"][c["player_id"]]} for c in cards]}
    monkeypatch.setattr(api.espn_season, "roster_state", roster_state)
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: None)
    def roster_flags(lg):
        state["flag_reads"] += 1
        return {i: {"locked": i in state["locked"], "slot": s} for i, s in state["slots"].items()}
    monkeypatch.setattr(api.espn_season, "roster_flags", roster_flags)
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)  # these model real writes (move_lineup is stubbed)
    monkeypatch.setattr(api.espn_season, "load", lambda: {"starters": {"RB/WR/TE": 1}})
    monkeypatch.setattr(api.espn_write, "pending_state", lambda: ([], state["period"]))
    sent = []
    monkeypatch.setattr(api.espn_write, "move_lineup",
                        lambda items, week: sent.append((items, week)) or state["slots"].update({p: t for p, _, t in items}))
    client.state, client.sent = state, sent
    return client


ORIGINAL_WRITES = api.espn_write.writes
SWAP = {"moves": [{"player_id": 3, "to_slot": "RB/WR/TE"}, {"player_id": 2, "to_slot": "BE"}]}
ORIGINAL_MOVE_LINEUP = api.espn_write.move_lineup


def _titles():
    return [r["title"] for r in db.decisions()]


def test_lineup_swap_writes_once_logs_it_and_returns_the_fresh_roster(lineup_env):
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200
    assert lineup_env.sent == [([(3, "BE", "RB/WR/TE"), (2, "RB/WR/TE", "BE")], 3)]
    assert {c["player_id"]: c["slot"] for c in r.json()["cards"]} == {3: "RB/WR/TE", 2: "BE"}, "read back, not echoed"
    assert _titles() == ["Week 3 lineup: Bench to FLEX, Flex to the bench"]
    assert r.json()["decision"] == {"id": db.decisions()[0]["id"], "needs_rationale": False}, "Bench START in, Flex BENCH out: the call"


def test_a_decision_the_db_cannot_take_never_fails_the_move(lineup_env, monkeypatch, caplog):
    def broken(row, season=None):
        raise db.sa.exc.OperationalError("insert", {}, Exception("disk I/O error"))
    monkeypatch.setattr(api.db, "add_decision", broken)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is None and "not recorded" in caplog.text


def test_a_test_server_can_record_fake_moves(lineup_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    r = lineup_env.post("/lineup", json=SWAP)
    row = db.decisions()[0]
    assert row["title"].startswith("[fake] Week 3 lineup") and row["facts"]["fake"] is True and r.json()["decision"]["id"] == row["id"]


def test_logging_off_in_settings_records_no_move(lineup_env):
    assert lineup_env.get("/decisions/prefs").json() == {"log": True, "page": True}
    assert lineup_env.post("/decisions/prefs", json={"log": False}).json() == {"log": False, "page": True}
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is None and db.decisions() == []
    assert lineup_env.post("/decisions/prefs", json={"page": "no"}).status_code == 422
    assert lineup_env.get("/decisions/prefs").json() == {"log": False, "page": True}, "a refused change leaves the switches"


def test_turning_the_decisions_page_off_turns_logging_off(lineup_env):
    assert lineup_env.post("/decisions/prefs", json={"page": False}).json() == {"log": False, "page": False}
    assert lineup_env.post("/decisions/prefs", json={"log": True}).json() == {"log": False, "page": False}, "no logging without the page"
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is None and db.decisions() == []
    assert lineup_env.post("/decisions/prefs", json={"page": True, "log": True}).json() == {"log": True, "page": True}


def test_unreadable_decision_prefs_never_fail_a_move(lineup_env, monkeypatch, caplog):
    def broken(key, default=None):
        raise db.sa.exc.OperationalError("select", {}, Exception("disk I/O error"))
    monkeypatch.setattr(api.db, "get_state", broken)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is not None and "defaults used" in caplog.text


def test_lineup_keeps_the_row_the_moved_player_took(lineup_env):
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    assert db.seat_map() == {3: 2}, "Bench stands in Flex's place in ESPN's order; Flex went to the bench"


def test_lineup_fake_writes_validate_the_second_swap_against_the_first(lineup_env, monkeypatch):
    # FFL_WRITES=0: ESPN never moves, the fake store does; roster_state shows the fake slots, and so must the validator
    monkeypatch.setenv("FFL_WRITES", "0")
    monkeypatch.setattr(api.espn_write, "_FAKE_SLOTS", {})
    monkeypatch.setattr(api.espn_write, "move_lineup", ORIGINAL_MOVE_LINEUP)
    monkeypatch.setattr(api.espn_write, "writes", ORIGINAL_WRITES)
    real = api.espn_season.roster_state
    monkeypatch.setattr(api.espn_season, "roster_state", lambda week=None, flags=None: {
        **(d := real(week)), "cards": [{**c, "slot": api.espn_write.fake_slots().get(c["player_id"], c["slot"])} for c in d["cards"]]})
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    back = {"moves": [{"player_id": 3, "to_slot": "BE"}, {"player_id": 2, "to_slot": "RB/WR/TE"}]}
    r = lineup_env.post("/lineup", json=back)
    assert r.status_code == 200
    assert api.espn_write.fake_slots() == {3: "BE", 2: "RB/WR/TE"}, "the second move ran from the first one's slots"
    assert {c["player_id"]: c["slot"] for c in r.json()["cards"]} == {3: "BE", 2: "RB/WR/TE"}
    assert db.decisions() == [], "a fake move is not a decision: logged, never journaled"


def test_lineup_read_back_reuses_the_flags_it_read_with_the_moves_applied(lineup_env):
    lineup_env.get("/roster")
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200
    assert lineup_env.state["flag_reads"] == 1, "one mRoster read per move: the read-back reuses it"
    assert {i: f["slot"] for i, f in lineup_env.state["readback_flags"].items()} == {3: "RB/WR/TE", 2: "BE"}


def test_lineup_fake_move_logs_instead_of_journaling(lineup_env, monkeypatch, caplog):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    with caplog.at_level("INFO", logger=api.log.name):
        assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    assert db.decisions() == []
    assert "Week 3 lineup: Bench to FLEX" in caplog.text


def test_lineup_maps_errors_from_the_concurrent_reads_as_before(lineup_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "pending_state", lambda: (_ for _ in ()).throw(api.espn_write.WriteError(401, "x")))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["error"] == "ESPN login expired, refresh .env"
    monkeypatch.setattr(api.espn_write, "pending_state", lambda: (_ for _ in ()).throw(api.espn_write.WriteError(0, "Timeout: t")))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["error"] == "ESPN couldn't be reached, try again"
    monkeypatch.setattr(api.espn_season, "roster_flags", lambda lg: (_ for _ in ()).throw(RuntimeError("mRoster down")))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"] == {"source": "espn", "error": "mRoster down"}
    assert lineup_env.sent == []


def test_lineup_refuses_a_player_whose_game_started_since_the_page_loaded(lineup_env):
    lineup_env.state["locked"] = {2}
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 409 and r.json()["detail"]["errors"] == ["Flex: his game has started"]
    assert lineup_env.sent == []


def test_lineup_refuses_when_espns_week_rolled(lineup_env, monkeypatch):
    refreshed = []
    monkeypatch.setattr(api.espn_season, "refresh_league", lambda: refreshed.append(1))
    lineup_env.get("/roster")  # caches the old week's page
    builds = lineup_env.state["builds"]
    lineup_env.state["period"] = 4
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 409 and "week changed" in r.json()["detail"]["error"] and lineup_env.sent == []
    assert refreshed == [1], "the cached League is dropped, or Reload rebuilds the same old week"
    lineup_env.get("/roster")
    assert lineup_env.state["builds"] > builds, "the roster page rebuilds on the next GET"


def test_lineup_refuses_a_second_move_while_one_runs(lineup_env):
    assert api._LINEUP.acquire(blocking=False)
    try:
        assert lineup_env.post("/lineup", json=SWAP).status_code == 409
    finally:
        api._LINEUP.release()


def test_lineup_reports_espns_refusal(lineup_env, monkeypatch):
    def boom(items, week):
        raise api.espn_write.WriteError(401, "nope")
    monkeypatch.setattr(api.espn_write, "move_lineup", boom)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["error"] == "ESPN login expired, refresh .env"


def test_lineup_read_back_failure_says_it_moved(lineup_env, monkeypatch):
    real = api.espn_season.roster_state
    def after_write(week=None, flags=None):
        if lineup_env.sent:
            raise RuntimeError("espn down")
        return real(week)
    monkeypatch.setattr(api.espn_season, "roster_state", after_write)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["moved"] is True and len(lineup_env.sent) == 1
    monkeypatch.setattr(api.espn_season, "roster_state", real)  # ESPN is back
    g = lineup_env.get("/roster")
    assert {c["player_id"]: c["slot"] for c in g.json()["cards"]} == {3: "RB/WR/TE", 2: "BE"}, "rebuilt, not the cached pre-move page"


def test_lineup_read_back_failure_still_journals_the_move(lineup_env, monkeypatch):
    real = api.espn_season.roster_state
    monkeypatch.setattr(api.espn_season, "roster_state",
                        lambda week=None, flags=None: (_ for _ in ()).throw(RuntimeError("espn down")) if lineup_env.sent else real(week))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["moved"] is True and len(lineup_env.sent) == 1
    assert _titles() == ["Week 3 lineup: Bench to FLEX, Flex to the bench"]


def test_lineup_timeout_may_have_moved_so_it_says_so_journals_it_and_drops_the_page(lineup_env, monkeypatch):
    def slow(items, week):
        raise api.requests.Timeout("15s")
    lineup_env.get("/roster")
    monkeypatch.setattr(api.espn_write, "move_lineup", slow)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["moved"] is True and "did not answer" in r.json()["detail"]["error"]
    assert db.decisions()[0]["facts"]["uncertain"] is True
    assert not api.cache.has("roster")


def test_lineup_posts_the_from_slots_espn_has_now_not_the_cached_ones(lineup_env):
    lineup_env.get("/roster")  # caches the page with 3 on the bench, 2 at FLEX
    lineup_env.state["slots"] = {3: "RB/WR/TE", 2: "BE"}  # moved on ESPN since
    r = lineup_env.post("/lineup", json={"moves": [{"player_id": 3, "to_slot": "BE"}, {"player_id": 2, "to_slot": "RB/WR/TE"}]})
    assert r.status_code == 200
    assert lineup_env.sent == [([(3, "RB/WR/TE", "BE"), (2, "BE", "RB/WR/TE")], 3)]


def test_cache_put_beats_a_revalidate_that_started_before_it():
    c = api.Cache(60.0)
    gate, release = threading.Event(), threading.Event()
    def slow():
        gate.set()
        release.wait(5)
        return "old"
    c.get_or_build("k", lambda: "first")
    c._d["k"].fn = slow
    c.expire("k")
    t = threading.Thread(target=c._rebuild, args=("k",))
    t.start()
    gate.wait(5)
    c.put("k", "new")
    release.set()
    t.join()
    assert c._d["k"].payload == "new"


def test_lineup_fake_ir_move_round_trips(lineup_env, monkeypatch):
    # FFL_WRITES=0: the IR occupant to the bench and an Out bench player onto IR, through the fake store, and back
    monkeypatch.setenv("FFL_WRITES", "0")
    monkeypatch.setattr(api.espn_write, "_FAKE_SLOTS", {})
    monkeypatch.setattr(api.espn_write, "move_lineup", ORIGINAL_MOVE_LINEUP)
    monkeypatch.setattr(api.espn_write, "writes", ORIGINAL_WRITES)
    cards = [{"player_id": 5, "name": "Hurt", "pos": "WR", "slot": "IR", "injury": "INJURY_RESERVE", "call": "IR",
              "gap": None, "group": "ir", "locked": False},
             {"player_id": 7, "name": "Sore", "pos": "RB", "slot": "BE", "injury": "OUT", "call": "OUT",
              "gap": None, "group": "bench", "locked": False}]
    monkeypatch.setattr(api.espn_season, "roster_state", lambda week=None, flags=None: {
        "week": 3, "live": False, "empty_slots": [], "writes": False,
        "cards": [{**c, "slot": api.espn_write.fake_slots().get(c["player_id"], c["slot"])} for c in cards]})
    monkeypatch.setattr(api.espn_season, "roster_flags",
                        lambda lg: {c["player_id"]: {"locked": False, "slot": c["slot"]} for c in cards})
    monkeypatch.setattr(api.espn_season, "load", lambda: {"starters": {"RB/WR/TE": 1}, "ir_size": 1})
    r = lineup_env.post("/lineup", json={"moves": [{"player_id": 5, "to_slot": "BE"}, {"player_id": 7, "to_slot": "IR"}]})
    assert r.status_code == 200, r.json()
    assert {c["player_id"]: c["slot"] for c in r.json()["cards"]} == {5: "BE", 7: "IR"}
    assert api.espn_write.fake_slots() == {5: "BE", 7: "IR"}
    # validated against the fake slots: IR is full now
    r = lineup_env.post("/lineup", json={"moves": [{"player_id": 5, "to_slot": "IR"}]})
    assert r.status_code == 409 and r.json()["detail"]["errors"] == ["IR: 2 players for 1 spots"]
    assert lineup_env.post("/lineup", json={"moves": [{"player_id": 7, "to_slot": "BE"}, {"player_id": 5, "to_slot": "IR"}]}).status_code == 200
    assert api.espn_write.fake_slots() == {5: "IR", 7: "BE"}
    assert db.decisions() == [], "a fake move is not a decision"


@pytest.fixture
def sit_env(client, monkeypatch):
    from ffl import season
    from tests.test_season import TWO_QB, roster
    state = {"builds": 0}

    def roster_state(week=None, flags=None):
        state["builds"] += 1
        rows = api.espn_season.with_sits(roster(), db.sat_ids(3))
        return {"week": 3, "live": False, "empty_slots": [], "writes": False,
                "cards": season.roster_cards(rows, TWO_QB, 3)}
    monkeypatch.setattr(api.espn_season, "roster_state", roster_state)
    monkeypatch.setattr(api.espn_season, "load", lambda: TWO_QB)
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    client.state = state
    return client


def test_sit_remakes_the_call_without_an_espn_read_and_journals_it(sit_env):
    sit_env.get("/roster")
    r = sit_env.post("/roster/sit/3", json={"sat": True})   # QB C, the best QB
    assert r.status_code == 200
    cards = {c["player_id"]: c for c in r.json()["cards"]}
    assert cards[3]["call"] == "SIT" and cards[3]["sat"] is True and cards[2]["call"] == "START"
    assert sit_env.state["builds"] == 1, "the cards are re-called, not rebuilt from ESPN"
    assert db.sat_ids(3) == {3}
    assert _titles() == ["Week 3 sit: QB C"] and r.json()["decision"]["needs_rationale"] is True
    assert {c["player_id"]: c["call"] for c in sit_env.get("/roster").json()["cards"]}[3] == "SIT", "cached as sat"


def test_unsit_puts_him_back_and_a_repeat_is_not_journaled_twice(sit_env):
    sit_env.post("/roster/sit/3", json={"sat": True})
    assert sit_env.post("/roster/sit/3", json={"sat": True}).json()["decision"] is None
    r = sit_env.post("/roster/sit/3", json={"sat": False})
    assert {c["player_id"]: c["call"] for c in r.json()["cards"]}[3] == "START" and db.sat_ids(3) == set()
    assert sorted(_titles()) == ["Week 3 sit: QB C", "Week 3 unsit: QB C"]


def test_a_dev_api_sits_but_does_not_journal(sit_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    assert sit_env.post("/roster/sit/3", json={"sat": True}).status_code == 200
    assert db.sat_ids(3) == {3} and db.decisions() == []


def test_sit_refuses_a_player_off_the_roster_and_a_running_move(sit_env):
    assert sit_env.post("/roster/sit/999", json={"sat": True}).status_code == 404
    api._LINEUP.acquire()
    try:
        assert sit_env.post("/roster/sit/3", json={"sat": True}).status_code == 409
    finally:
        api._LINEUP.release()
    assert db.sat_ids(3) == set()


def test_notify_routes(client, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "PUB")
    cfg = client.get("/notify/config").json()
    assert cfg["public_key"] == "PUB" and cfg["prefs"]["move"] is True and cfg["enabled"] is False
    sub = {"endpoint": "https://push/x", "keys": {"p256dh": "k", "auth": "a"}, "ua": "iPhone"}
    assert client.post("/notify/subscribe", json=sub).status_code == 200
    assert client.get("/notify/config").json()["devices"] == 1
    assert client.post("/notify/prefs", json={"injury": False}).json()["injury"] is False
    assert client.post("/notify/prefs", json={"quiet": ["99:00", "07:00"]}).status_code == 422
    assert client.post("/notify/unsubscribe", json={"endpoint": "https://push/x"}).status_code == 200
    assert client.get("/notify/recent").json() == {"notices": []}


def test_notify_test_503s_when_disabled(client):
    assert client.post("/notify/test").status_code == 503


def _move_lg():
    topics = {"topics": [{"date": 1788937502756, "messages": [{"messageTypeId": 180, "to": 6, "targetId": 5083754, "from": 0}]}]}
    team = SimpleNamespace(team_id=6, team_name="Henry", roster=[])
    return SimpleNamespace(year=2026, player_map={5083754: "Ryan Flournoy", "Ryan Flournoy": 5083754},
                           get_team_data=lambda tid: team,
                           espn_request=SimpleNamespace(league_get=lambda extend="", params=None, headers=None: topics))


ROSTER_KEYS = ["transactions", "roster", "waivers", "week:current", "week:5:3", "league:current", "team:3",
               "starters:current"]


def test_a_build_a_move_drops_partway_is_not_installed():
    """A page whose build began before a move read the rosters from before it: once the move drops the page, that
    build must not install itself (a first build or a revalidate), or the rewarm finds it fresh and serves it."""
    c = api.Cache(120)

    def before_the_move():
        c.drop_prefixes(("roster",))      # the move lands while this build runs
        return {"old": True}
    payload, _ = c.get_or_build("roster", before_the_move)
    assert payload == {"old": True} and not c.has("roster"), "the caller gets it; the cache does not keep it"

    c.get_or_build("roster", lambda: {"n": 1})
    c._d["roster"].expires = 0.0
    c._d["roster"].fn = before_the_move
    c.get_or_build("roster", before_the_move)  # stale: serves n=1 and revalidates behind
    c.join()
    assert not c.has("roster"), "the revalidate that the move overtook leaves the key dropped"


def test_every_rewarm_thread_is_joined():
    c = api.Cache(120)
    gate = threading.Event()
    api._start_rewarm(c, [("a", lambda: gate.wait(2) or {"a": 1}, 120.0)])
    api._start_rewarm(c, [("b", lambda: {"b": 1}, 120.0)])
    gate.set()
    c.join()
    assert c.has("a") and c.has("b")


def test_a_logged_move_refreshes_every_page_that_shows_rosters(client, monkeypatch):
    """A move changes rosters, the pool, the boards and the log: every page built before it is stale. Whichever
    path logged it (the tick, a page build, the reconcile), each such page is dropped (never served once more from
    before the move), rebuilt behind, and the /live stream says "moves" so an open page refetches."""
    import math
    from ffl import espn_season, journal, live
    built = {}

    def builder(key):
        def fn():
            built[key] = built.get(key, 0) + 1
            return {"key": key, "n": built[key]}
        return fn
    for key in ROSTER_KEYS + ["player:9", "logo:3", "draft_picks"]:
        api.cache.get_or_build(key, builder(key))
    monkeypatch.setattr(espn_season, "_BOXES", {4: (math.inf, None, ["done"]), 5: (9e18, None, ["now"])})
    monkeypatch.setattr(live, "STATE", live.State())

    journal.sync_transactions(_move_lg())
    api.cache.join()

    assert all(built[k] == 2 for k in ROSTER_KEYS), built          # rebuilt behind the move
    assert built["player:9"] == 1 and not api.cache.has("player:9")  # dropped, built on its next open
    assert built["logo:3"] == built["draft_picks"] == 1 and api.cache.has("logo:3")
    assert list(espn_season._BOXES) == [4], "the current week's box scores are refetched, finished weeks kept"
    assert live.STATE.moves == 1 and live.STATE.version == 0

    journal.sync_transactions(_move_lg())  # nothing new: nothing rebuilt, no tick
    api.cache.join()
    assert built["roster"] == 2 and live.STATE.moves == 1


def test_notify_tick_409s_while_another_runs(client, monkeypatch):
    monkeypatch.setattr(api.notify, "tick", lambda: {"moves": 0})
    monkeypatch.setattr(api, "pregame_pull", lambda: [])
    monkeypatch.setattr(api, "correction_pass", lambda: {})
    assert api._NOTIFY.acquire(blocking=False)
    try:
        assert client.post("/notify/tick").status_code == 409
    finally:
        api._NOTIFY.release()
    assert client.post("/notify/tick").status_code == 200


# decisions: the journal's list, presets, edit, and a player's decisions on his transactions
def _dec(**kw):
    row = {"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app", "title": "Week 6 lineup: A to FLEX",
           "decision": "A (WR) BE → FLEX", "rationale": None, "player_ids": [11, 12], **kw}
    return db.add_decision(row)


def test_decisions_list_filters(client):
    a = _dec()
    b = _dec(at="2026-10-03T12:00:00-04:00", week=5, kind="claims", title="Week 5 waiver claims", rationale=["best_ros"], outcome="hit")
    body = client.get("/decisions").json()
    assert [r["id"] for r in body["decisions"]] == [a, b] and body["weeks"] == [6, 5]
    assert [r["id"] for r in client.get("/decisions?week=5").json()["decisions"]] == [b]
    assert [r["id"] for r in client.get("/decisions?kind=lineup").json()["decisions"]] == [a]
    assert [r["id"] for r in client.get("/decisions?open=1").json()["decisions"]] == [a]


def test_presets_are_served_per_kind(client):
    p = client.get("/decisions/presets").json()
    assert p["lineup"][0] == {"key": "followed_call", "label": "Followed the call"} and p["claims"][-1]["key"] == "other"


def test_edit_a_decision(client):
    i = _dec()
    r = client.post(f"/decisions/{i}", json={"rationale": ["other"], "note": "gut on the matchup"})
    assert r.status_code == 200 and r.json()["rationale"] == ["other"] and r.json()["rationale_note"] == "gut on the matchup"
    r = client.post(f"/decisions/{i}", json={"outcome": "won the flex by 6"})
    assert r.json()["outcome"] == "won the flex by 6" and r.json()["outcome_at"] and r.json()["rationale"] == ["other"], "untouched"
    assert client.post(f"/decisions/{i}", json={"outcome": ""}).json()["outcome"] is None


def test_edit_refuses_a_reason_from_another_kind_or_an_empty_other(client):
    i = _dec()
    r = client.post(f"/decisions/{i}", json={"rationale": ["best_ros"]})
    assert r.status_code == 422 and "not a lineup reason" in r.json()["detail"]["error"]
    assert client.post(f"/decisions/{i}", json={"rationale": ["other"], "note": " "}).status_code == 422
    assert client.post("/decisions/9999", json={"outcome": "x"}).status_code == 404


def test_an_unchanged_reason_is_not_revalidated(client):
    # imported rows can hold Other with no note: the form sends the reason back unchanged with an outcome
    i = _dec(source="import", rationale=["other"], rationale_note=None)
    r = client.post(f"/decisions/{i}", json={"rationale": ["other"], "note": None, "outcome": "x"})
    assert r.status_code == 200 and r.json()["outcome"] == "x" and r.json()["rationale"] == ["other"]
    assert r.json()["rationale_note"] is None
    j = _dec(rationale=["matchup"])
    assert client.post(f"/decisions/{j}", json={"rationale": ["other"], "note": " "}).status_code == 422, "a changed one is"


def test_player_transactions_carry_his_decisions(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "transactions_state", lambda: {"moves": [], "teams": [], "stale_since": None})
    monkeypatch.setattr(api.espn_season, "draft_picks", lambda: [])
    api.cache.clear()
    i = _dec()
    _dec(player_ids=[99])
    assert [d["id"] for d in client.get("/player/11/transactions").json()["decisions"]] == [i]


def test_sources_status_reports_memory_over_the_db(client, monkeypatch):
    from ffl import db, sources
    db.put_state("source_status", {"cbs": {"ok_at": "a", "rows": {}, "error": None, "failing_since": None}})
    monkeypatch.setitem(sources.STATUS, "sharks", {"ok_at": "b", "rows": {"QB": 60}, "error": None, "failing_since": None})
    body = client.get("/sources/status").json()
    assert body["cbs"]["ok_at"] == "a" and body["sharks"]["rows"] == {"QB": 60}


def test_notify_tick_pulls_once_before_a_kickoff(client, monkeypatch):
    """Inactives are out 90 minutes before kickoff: the tick inside the lead expires every page and source, once."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(api.notify, "tick", lambda: {"moves": 0})
    kick = datetime.now(timezone.utc) + timedelta(minutes=60)
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: "LG")
    monkeypatch.setattr(api.espn_season, "kickoffs_by_week", lambda lg: {6: [kick, kick]})
    client.get("/week/3")
    assert client.post("/notify/tick").json()["pregame"] == [kick.isoformat()]
    api.cache.join()
    assert client.calls["week"] == 2, "rebuilt behind the tick"
    assert client.post("/notify/tick").json()["pregame"] == [], "once per kickoff"
    monkeypatch.setattr(api.espn_season, "kickoffs_by_week", lambda lg: (_ for _ in ()).throw(RuntimeError("espn down")))
    assert client.post("/notify/tick").json()["pregame"] == {"error": "espn down"}


def test_trace_appends_a_stamped_row(client, monkeypatch, tmp_path):
    monkeypatch.setattr(api, "TRACE_PATH", tmp_path / "trace.jsonl")
    assert client.post("/trace", json={"event": "click", "fields": {"url": "/transactions"}}).status_code == 200
    assert client.post("/trace", json={"event": "hello"}).status_code == 200
    rows = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert [r["event"] for r in rows] == ["click", "hello"]
    assert rows[0]["url"] == "/transactions" and rows[0]["at"][:4].isdigit()


# --- page payloads in SQL (ffl-iq3): no cold load after a restart ---

def test_a_page_built_before_a_restart_is_served_at_once_then_rebuilt():
    api.Cache(120, persist=True).get_or_build("roster", lambda: {"n": 1})
    after = api.Cache(120, persist=True)
    after.load()
    calls = []

    def build():
        calls.append(1)
        return {"n": 2}
    payload, stale = after.get_or_build("roster", build)
    assert payload == {"n": 1} and stale is None, "the stored page, at once, not flagged as a source failure"
    after.join()
    assert calls == [1] and after._d["roster"].payload == {"n": 2}
    assert [p["payload"] for p in db.pages()] == [{"n": 2}], "the rebuild is written through"


def test_a_loaded_page_is_never_fresh():
    api.Cache(120, persist=True).get_or_build("league:current", lambda: {"n": 1})
    after = api.Cache(120, persist=True)
    after.load()
    assert after._d["league:current"].expires == 0.0
    after.rebuild_expired()  # /refresh: a loaded page with no builder yet is left for its first request
    assert after._d["league:current"].payload == {"n": 1}


def test_only_pages_that_survive_json_are_stored():
    c = api.Cache(120, persist=True)
    c.get_or_build("roster", lambda: {1: "int keys come back as strings"})
    c.get_or_build("waivers", lambda: {"pair": (1, 2)})
    c.get_or_build("starters:current", lambda: {"ids": [1]})
    c.get_or_build("logo:3", lambda: (b"png", "image/png"))
    c.get_or_build("draft_picks", lambda: {"picks": []})
    c.get_or_build("team:3", lambda: {"ok": True})
    assert [p["key"] for p in db.pages()] == ["team:3"]


def test_a_dropped_page_never_comes_back_from_the_db():
    c = api.Cache(120, persist=True)
    for k in ("roster", "team:3", "week:current", "league:current"):
        c.get_or_build(k, lambda: {"k": 1})
    c.drop_prefixes(("roster", "team"))
    c.drop("week:current")
    assert [p["key"] for p in db.pages()] == ["league:current"]
    c.clear()
    assert db.pages() == []


def test_a_move_deletes_stored_pages_never_loaded():
    db.put_page("team:3", {"k": 1}, time.time())
    db.put_page("teams", {"k": 1}, time.time())  # another prefix that merely starts the same
    c = api.Cache(120, persist=True)  # boot's load failed, say: nothing in memory
    assert c.drop_prefixes(("team",)) == []
    assert [p["key"] for p in db.pages()] == ["teams"]


def test_a_build_a_move_overtakes_writes_no_row():
    c = api.Cache(120, persist=True)

    def before_the_move():
        c.drop_prefixes(("roster",))
        return {"old": True}
    c.get_or_build("roster", before_the_move)
    assert db.pages() == []
    c.get_or_build("roster", lambda: {"n": 1})
    c._d["roster"].expires = 0.0
    c._d["roster"].fn = before_the_move
    c.get_or_build("roster", before_the_move)
    c.join()
    assert db.pages() == [], "the revalidate the move overtook leaves no row behind"


def test_put_writes_through():
    c = api.Cache(120, persist=True)
    c.get_or_build("roster", lambda: {"n": 1})
    c.put("roster", {"n": 5})
    assert [p["payload"] for p in db.pages()] == [{"n": 5}]


def test_old_player_cards_are_pruned_at_load():
    db.put_page("player:9", {"old": True}, time.time() - 8 * 86400)
    db.put_page("player:8", {"new": True}, time.time())
    db.put_page("week:2", {"old": True}, time.time() - 8 * 86400)
    c = api.Cache(120, persist=True)
    c.load()
    assert sorted(c._d) == ["player:8", "week:2"]
    assert sorted(p["key"] for p in db.pages()) == ["player:8", "week:2"]


def test_a_failed_write_keeps_the_page(monkeypatch):
    def broken(*a, **k):
        raise db.DBError("disk full")
    monkeypatch.setattr(api.db, "put_page", broken)
    c = api.Cache(120, persist=True)
    assert c.get_or_build("roster", lambda: {"n": 1})[0] == {"n": 1}
    assert c.has("roster")


def test_startup_serves_the_stored_page_on_the_first_hit(client):
    db.put_page("week:3", {"week": 3, "live": False, "me": {"team": "stored"}, "opp": {"team": "opp"}}, time.time() - 3600)
    with TestClient(api.app) as c:
        assert c.get("/week/3").json()["me"]["team"] == "stored"
        api.cache.join()
        assert c.get("/week/3").json()["me"]["team"] == "me"


def test_a_cold_key_is_built_once_for_concurrent_callers():
    from tests.test_flight import park, race
    c, calls = api.Cache(60.0), []

    def build():
        park(c._flight, "k", 1)
        calls.append(1)
        return {"n": 1}

    assert race(2, lambda: c.get_or_build("k", build)) == [({"n": 1}, None)] * 2
    assert calls == [1] and c._d["k"].payload == {"n": 1}


def test_a_failed_shared_first_build_is_a_502_for_every_caller():
    from tests.test_flight import park, race
    c, calls = api.Cache(60.0), []

    def build():
        park(c._flight, "k", 1)
        calls.append(1)
        raise requests.ConnectionError("espn down")

    out = race(2, lambda: c.get_or_build("k", build))
    assert calls == [1]
    assert all(isinstance(e, api.HTTPException) and e.status_code == 502 for e in out)
    assert not c.has("k")


def test_a_move_during_a_shared_first_build_hands_it_out_but_never_installs_it():
    from tests.test_flight import park, race
    c = api.Cache(60.0)

    def build():
        park(c._flight, "week:current", 1)
        c.drop_prefixes(("week",))
        return {"n": 1}

    assert race(2, lambda: c.get_or_build("week:current", build)) == [({"n": 1}, None)] * 2
    assert not c.has("week:current")


def test_a_rewarm_that_joins_a_build_from_before_the_move_builds_again():
    # the drop lands between a first build's install and its flight closing: the rewarm joins that flight, which
    # hands out its payload but never installs, so the rewarm builds once more and the page is there when it ticks
    from tests.test_flight import park
    c, calls, release = api.Cache(60.0), [], threading.Event()

    def build():
        calls.append(1)
        if len(calls) == 1:
            release.wait(5)
        return {"n": len(calls)}

    leader = threading.Thread(target=lambda: c.get_or_build("week:current", build))
    leader.start()
    for _ in range(500):
        if calls:
            break
        time.sleep(0.01)
    c._d["week:current"] = api.Entry(time.time() + 60, time.time(), {"n": 0}, build, 60.0)  # the install, landed
    dropped = c.drop_prefixes(("week",))
    rewarm = threading.Thread(target=api._rewarm, args=(c, dropped))
    rewarm.start()
    park(c._flight, "week:current", 1)
    release.set()
    leader.join(5)
    rewarm.join(5)
    assert calls == [1, 1]
    assert c._d["week:current"].payload == {"n": 2}


def test_expire_everything_keeps_finished_weeks(monkeypatch):
    import math
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "_BOXES", {4: (math.inf, None, ["done"]), 5: (9e18, None, ["now"])})
    monkeypatch.setattr(espn_season, "_GAP_GAMES", {(7, 3): [{"week": 3}]})
    api.expire_everything()
    assert list(espn_season._BOXES) == [4] and (7, 3) in espn_season._GAP_GAMES


# ffl-7sq: the stat-correction pass on the notify tick, and past boards' stored feeds
def _corr(week, pid=1, team=7):
    return {"at": "2026-10-14T12:00:00-04:00", "week": week, "player_id": pid, "team_id": team, "delta": 1.5,
            "total": 21.5, "text": "Stat correction: 20.0 → 21.5", "match": "correction", "quarter": None, "clock": None}


def _stub_correct(monkeypatch, out):
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda: "LG")
    monkeypatch.setattr(api.espn_season, "correct_boxes", lambda lg, now=None: {**out, "plays": list(out.get("plays", []))})


def test_notify_tick_answers_with_the_corrections(client, monkeypatch):
    monkeypatch.setattr(api.notify, "tick", lambda: {"moves": 0})
    monkeypatch.setattr(api, "pregame_pull", lambda: [])
    _stub_correct(monkeypatch, {"weeks": {}, "changed": []})
    assert client.post("/notify/tick").json()["corrections"] == {"weeks": {}, "changed": []}
    monkeypatch.setattr(api.espn_season, "correct_boxes", lambda lg, now=None: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.post("/notify/tick")
    assert r.status_code == 200 and r.json()["corrections"] == {"error": "espn down"}


def test_correction_plays_go_to_the_runner_store_for_its_week_else_the_table(client, monkeypatch):
    added = []
    monkeypatch.setattr(api.app.state, "runner", SimpleNamespace(week=6, store=SimpleNamespace(add=added.extend)), raising=False)
    _stub_correct(monkeypatch, {"weeks": {}, "changed": [6, 4], "plays": [_corr(6), _corr(4, pid=2)]})
    out = api.correction_pass()
    assert [p["week"] for p in added] == [6] and "plays" not in out
    assert [r["player_id"] for r in db.plays_rows(4)] == [2] and db.plays_rows(6) == []


def test_a_changed_week_expires_its_pages_and_the_roster_only(client, monkeypatch):
    for k in ("league:5", "league:4", "roster"):
        api.cache.get_or_build(k, lambda: {"k": 1})
    _stub_correct(monkeypatch, {"weeks": {}, "changed": [5]})
    api.correction_pass()
    exp = {k: api.cache._d[k].expires for k in ("league:5", "league:4", "roster")}
    assert exp["league:5"] == 0.0 and exp["roster"] == 0.0 and exp["league:4"] > 0.0


def test_a_past_board_shows_its_stored_feed_for_its_two_teams_newest_first(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None: {"week": week or 3, "live": False, "me": {"team_id": 7}, "opp": {"team_id": 3}})
    monkeypatch.setattr(api.app.state, "runner", SimpleNamespace(week=6, store=_store([])), raising=False)
    db.add_plays([_corr(4, pid=1, team=7), {**_corr(4, pid=2, team=3), "at": "2026-10-14T13:00:00-04:00"},
                  _corr(4, pid=3, team=9)])
    r = client.get("/week/4").json()["plays"]
    assert [(p["player_id"], p["side"]) for p in r] == [(2, "opp"), (1, "me")]


def test_a_settled_week_is_not_read_again_after_expire_everything(client, monkeypatch):
    from ffl import espn_season
    doc = {"matchups": [], "pro_schedule": {}, "ratings": {}}
    db.put_box_doc(4, doc, espn_season.box_hash(doc), 1.0, settled=True, final=True)
    monkeypatch.setattr(espn_season, "_fetch_box_doc", lambda lg, w: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setattr(espn_season, "_parse_box_doc", lambda lg, d: ["BOX"])
    api.expire_everything()
    lg = SimpleNamespace(espn_request=None, current_week=6)
    assert espn_season.box_scores(lg, 4) == ["BOX"]
