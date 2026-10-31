"""API glue: caching, stale fallback, validation. espn_season is stubbed; no network."""
import json
import threading
from datetime import datetime, timedelta, timezone
import time
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient

from ffl import api, db, leagues
from tests.home import HOME_KEY, home_league


@pytest.fixture
def client(monkeypatch, tmp_path):
    calls = {"week": 0}

    def fake_week(week=None, **k):
        calls["week"] += 1
        return {"week": week or 3, "live": False, "me": {"team": "me"}, "opp": {"team": "opp"}, "stale": None}

    monkeypatch.setattr(api.espn_season, "week_state", fake_week)
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None, **k: {"week": 3, "positions": {"RB": []}})
    monkeypatch.setattr(api.espn_season, "league_state", lambda week=None, **k: {"week": week or 3, "standings": [], "scoreboard": []})
    monkeypatch.setattr(api.espn_season, "team_state", lambda team_id, **k: {
        "week": 3, "live": False, "mine": team_id == 7, "club": {"team_id": team_id, "rank": 2},
        "side": {"team_id": team_id, "lineup": [{"player_id": 1, "actual": 0.0, "team": "DET"}], "bench": [], "ir": []}})
    monkeypatch.setattr(api.espn_season, "roster_state",
                        lambda week=None, **k: {"week": 3, "live": False, "cards": [
                            {"player_id": 1, "name": "A", "pos": "RB", "team": "DET", "bye": False, "injury": "ACTIVE",
                             "proj": 12.0, "spread": 1.0, "game_played": 0, "opponent": "GB", "kickoff": None,
                             "return_week": None, "injury_note": None, "call": "START", "close_call": False,
                             "reason": "starts at RB: no bench alternative", "group": "starter", "gap": None}]})
    monkeypatch.setattr(api.outlook.news, "gather", lambda cards, week: {c["player_id"]: {"depth": None, "teammates": [], "stories": []} for c in cards})
    monkeypatch.setattr(api.outlook, "OUTLOOK_DIR", tmp_path / "outlook")
    # start()'s thread looks LASTS up by name at call time, so a fresh dict per test keeps runs apart
    monkeypatch.setattr(api.outlook, "LASTS", {})
    monkeypatch.setattr(api, "sync_all", lambda **k: {"transactions": 0})
    api.cache_for(home_league()).clear()
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
                        lambda week=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    client.post("/refresh")
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["stale_since"] is not None


def test_refresh_502s_when_espn_is_down(client, monkeypatch):
    monkeypatch.setattr(api, "sync_all", lambda **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.post("/refresh")
    assert r.status_code == 502 and r.json()["detail"] == {"source": "espn", "error": "espn down"}


def test_a_jobs_bare_refresh_refreshes_every_league(client, monkeypatch, request):
    """The documented curl names no league: as a job it is every league, one failing its own line."""
    from ffl import leagues
    leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)

    def sync(*, ref):
        if ref.key == "espn:999":
            raise RuntimeError("espn down")
        return {"transactions": True}
    monkeypatch.setattr(api, "sync_all", sync)
    request.getfixturevalue("as_job")
    r = client.post("/refresh")
    assert r.status_code == 200 and set(r.json()["leagues"]) == {HOME_KEY, "espn:999"}
    assert r.json()["leagues"][HOME_KEY]["synced"] == {"transactions": True}
    assert r.json()["leagues"]["espn:999"] == {"error": {"source": "espn", "error": "espn down"}}


def test_transactions_sync_answers_the_new_count(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda *a, **k: "LG")
    monkeypatch.setattr(api, "load", lambda *a, **k: {})
    monkeypatch.setattr(api, "sync_transactions", lambda lg, **k: [{}, {}])
    results = []
    monkeypatch.setattr(api, "sync_results", lambda lg, settings, **k: results.append(lg))
    r = client.post("/transactions/sync")
    assert r.status_code == 200 and r.json() == {"new": 2}
    assert results == ["LG"], "the hourly sync keeps the results table too"
    monkeypatch.setattr(api, "sync_transactions", lambda lg, **k: [])
    assert client.post("/transactions/sync").json() == {"new": 0}


def test_transactions_sync_502s_when_espn_is_down(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda *a, **k: "LG")
    monkeypatch.setattr(api, "sync_transactions", lambda lg, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    assert client.post("/transactions/sync").status_code == 502


def test_sync_all_rebuilds_the_league(monkeypatch):
    """The League caches current_week and the records; sync_results would otherwise rewrite
    the results table truncated to whatever week the process booted in."""
    hits = {"refresh": 0, "fresh": 0}
    monkeypatch.setattr(api.espn_season, "refresh_league",
                        lambda *a, **k: hits.__setitem__("refresh", hits["refresh"] + 1))
    monkeypatch.setattr(api.espn_season, "fresh_league",
                        lambda *a, **k: (hits.__setitem__("fresh", hits["fresh"] + 1), "LG")[1])
    monkeypatch.setattr(api, "load", lambda *a, **k: {})
    monkeypatch.setattr(api, "sync_transactions", lambda lg, **k: None)
    monkeypatch.setattr(api, "sync_results", lambda lg, s, **k: None)
    assert api.sync_all(ref=home_league()) == {"transactions": True, "results": True}
    assert hits == {"refresh": 1, "fresh": 1}


def test_stale_payload_served_when_source_fails(client, monkeypatch):
    client.get("/week/3")
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    api.cache_for(home_league()).expire_all()
    client.get("/week/3")  # answered from the old payload; the rebuild behind it fails
    api.cache_for(home_league()).join()
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["stale_since"] is not None


def test_expired_entry_is_served_at_once_and_rebuilt_behind(client, monkeypatch):
    """An expired payload is answered immediately; the rebuild runs behind the request."""
    import threading
    gate = threading.Event()
    seen = {"n": 0}

    def slow_week(week=None, **k):
        seen["n"] += 1
        if seen["n"] > 1:
            gate.wait(5)
        return {"week": 3, "live": False, "me": {"team": f"me{seen['n']}"}, "opp": None}

    monkeypatch.setattr(api.espn_season, "week_state", slow_week)
    assert client.get("/week/3").json()["me"]["team"] == "me1"
    api.cache_for(home_league()).expire_all()
    t0 = time.time()
    r = client.get("/week/3").json()
    assert time.time() - t0 < 1, "the request did not wait on the rebuild"
    assert r["me"]["team"] == "me1" and r["stale_since"] is None, "expired is not 'source down'"
    assert client.get("/week/3").json()["me"]["team"] == "me1"
    assert seen["n"] == 2, "one rebuild in flight per key, however many requests arrive"
    gate.set()
    api.cache_for(home_league()).join()
    assert client.get("/week/3").json()["me"]["team"] == "me2"


def test_failed_background_rebuild_keeps_the_old_payload_and_flags_it(client, monkeypatch):
    client.get("/week/3")
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    api.cache_for(home_league()).expire_all()
    assert client.get("/week/3").json()["stale_since"] is None, "not known to be down yet"
    api.cache_for(home_league()).join()
    r = client.get("/week/3")
    assert r.status_code == 200 and r.json()["me"]["team"] == "me" and r.json()["stale_since"] is not None
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None, **k: {"week": 3, "live": False, "me": {"team": "back"}, "opp": None})
    client.get("/week/3")  # still flagged and expired: this get starts the retry
    api.cache_for(home_league()).join()
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
        api.cache_for(home_league()).join()
    assert client.calls["week"] == 1
    assert all(api.cache_for(home_league()).has(k) for k in ("week:current", "roster", "league:current", "waivers"))


def test_logo_serves_bytes_and_404s_when_missing(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "team_logo", lambda tid, **k: (b"\x89PNG", "image/png"))
    r = client.get("/logo/7")
    assert r.status_code == 200 and r.content == b"\x89PNG" and r.headers["content-type"] == "image/png"
    monkeypatch.setattr(api.espn_season, "team_logo", lambda tid, **k: (_ for _ in ()).throw(LookupError("no logo")))
    r = client.get("/logo/8")
    assert r.status_code == 404


def test_502_when_nothing_cached(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "league_state", lambda week=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
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
                        lambda **k: calls.append(1) or {"me": 7, "current_week": 5, "moves": [], "teams": []})
    assert client.get("/transactions").json() == {"me": 7, "current_week": 5, "moves": [], "teams": [], "stale_since": None}
    client.get("/transactions")
    assert len(calls) == 1


def test_player_transactions_filter_the_cached_moves(client, monkeypatch):
    side = {"team_id": 6, "team": "T6", "abbrev": "T", "logo": "", "added": [{"player_id": 101, "name": "A"}], "dropped": []}
    move = {"id": "5-6", "ms": 5, "at": "2026-09-10T03:05", "kind": "waiver", "week": 1, "sides": [side]}
    monkeypatch.setattr(api.espn_season, "transactions_state",
                        lambda **k: {"me": 7, "current_week": 5, "moves": [move], "teams": []})
    monkeypatch.setattr(api.espn_season, "draft_picks", lambda **k: [
        {"overall": 24, "round": 3, "pick": 4, "team_id": 6, "team": "T6", "player_id": 101, "at": None}])
    got = client.get("/player/101/transactions").json()["history"]
    assert [h["kind"] for h in got] == ["waiver", "draft"], "the draft is the oldest entry"
    assert client.get("/player/999/transactions").json()["history"] == []


def test_player_transactions_survive_a_failed_draft_read(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "transactions_state", lambda **k: {"me": 7, "current_week": 5, "moves": [], "teams": []})

    def boom(**k):
        raise RuntimeError("espn down")

    monkeypatch.setattr(api.espn_season, "draft_picks", boom)
    assert client.get("/player/101/transactions").json()["history"] == []


def test_team_overlays_live_points(client, monkeypatch):
    monkeypatch.setattr(api.live.STATE, "leagues", {HOME_KEY: ({1: 14.5}, 3)})
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
    api.outlook.save(3, {1: {"hash": "h", "card_hash": api.outlook.card_hash(body["cards"][0]), "bullets": [take], "generated_at": "t"}}, ref=home_league())
    assert client.get("/roster").json()["cards"][0]["outlook"] == [take], "no TTL wait for new bullets"


def test_outlook_refresh_starts_a_run_and_answers_409_while_busy(client, monkeypatch):
    import threading
    gate = threading.Event()
    take = {"label": "Take", "text": "He starts.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    monkeypatch.setattr(api.outlook, "run_claude", lambda prompt, **kw: (gate.wait(5), [take])[1])
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
                        lambda week=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.post("/outlook/refresh")
    assert r.status_code == 502 and r.json()["detail"]["source"] == "espn"


def _fa_pool(monkeypatch):
    row = {"player_id": 7, "name": "B", "pos": "RB", "team": "MIA", "bye": False, "injury": "ACTIVE",
           "opponent": "BUF", "kickoff": None, "return_week": None, "injury_note": None, "add": 9.0}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None, **k: {"week": 3, "positions": {"RB": [row], "WR": []}})
    monkeypatch.setattr(api.outlook, "_WRITING", {})
    api.cache_for(home_league()).clear()
    return row


def test_waiver_outlook_is_written_on_a_post_and_read_on_a_get(client, monkeypatch):
    import threading
    _fa_pool(monkeypatch)
    gate = threading.Event()
    take = {"label": "Take", "text": "A stash.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    monkeypatch.setattr(api.outlook, "run_claude", lambda prompt, **kw: (gate.wait(5), [take])[1])
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
    st.update({HOME_KEY: ({1: 24.7}, 2)}, {"DET": {"status": "post"}}, {1: {"3": 200.0}})
    monkeypatch.setattr(api.live, "STATE", st)
    assert api._live_points(3, ref=home_league()) == {} and api._live_games(3, ref=home_league()) == {} and api._live_stats(3, ref=home_league()) == {}
    st.update({HOME_KEY: ({1: 24.7}, 3)}, {"DET": {"status": "post"}}, {1: {"3": 200.0}})
    assert api._live_points(3, ref=home_league()) == {1: 24.7}


def _lane_runner(**lane):
    """A runner with one lane (week, store, ...), whatever league it is asked for."""
    ln = SimpleNamespace(**lane)
    return SimpleNamespace(lanes=[ln], lane=lambda key: ln)


def _store(rows):
    return SimpleNamespace(latest=lambda n=None, teams=None: [r for r in rows if teams is None or r["team_id"] in teams][:n])


def test_week_plays_come_only_from_the_runner_scoring_that_week(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None, **k: {"week": week or 3, "live": False, "me": {"team_id": 7}, "opp": {"team_id": 3}})
    store = _store([{"delta": 6.0, "team_id": 7}])
    monkeypatch.setattr(api.app.state, "runner", _lane_runner(week=2, store=store), raising=False)
    assert client.get("/week").json()["plays"] == []
    api.cache_for(home_league()).clear()
    monkeypatch.setattr(api.app.state, "runner", _lane_runner(week=3, store=store), raising=False)
    assert client.get("/week").json()["plays"] == [{"delta": 6.0, "team_id": 7, "side": "me"}]


def test_every_board_carries_its_own_games_plays_sided_from_its_left(client, monkeypatch):
    """`/week/{n}?team=` opens any scoreboard matchup; it caches under its own key, so my week's
    board is never served for someone else's game, and each board's feed is its two teams' rows."""
    asked = []

    def fake_week(week=None, team=None, **k):
        asked.append(team)
        left, right = (7, 3) if team is None else (team, 5)
        return {"week": week or 3, "live": False, "me": {"team": f"t{left}", "team_id": left, "abbrev": "L"},
                "opp": {"team": "x", "team_id": right, "abbrev": "R"}, "mine": team is None}

    monkeypatch.setattr(api.espn_season, "week_state", fake_week)
    store = _store([{"delta": 6.0, "team_id": 7}, {"delta": 2.0, "team_id": 5}, {"delta": 1.0, "team_id": 4}])
    monkeypatch.setattr(api.app.state, "runner", _lane_runner(week=3, store=store), raising=False)
    assert [(p["delta"], p["side"]) for p in client.get("/week/3").json()["plays"]] == [(6.0, "me")]
    r = client.get("/week/3?team=4").json()
    assert r["me"]["team"] == "t4" and [(p["delta"], p["side"]) for p in r["plays"]] == [(2.0, "opp"), (1.0, "me")]
    client.get("/week/3?team=4")
    assert asked == [None, 4]
    every = client.get("/plays?team=4").json()
    assert every["mine"] is False and every["me"] == {"team_id": 4, "team": "t4", "abbrev": "L"}
    assert [p["delta"] for p in every["plays"]] == [2.0, 1.0]
    assert [p["delta"] for p in client.get("/plays?team=4&limit=1").json()["plays"]] == [2.0], "the newest few"
    assert [p["delta"] for p in client.get("/plays").json()["plays"]] == [6.0]


def test_plays_page_opens_the_week_it_was_linked_from(client, monkeypatch):
    """A finished week's board links its whole feed with `week`: the page reads that week's stored rows, not
    the current week's, and leads back to that week's board (`current` false); my current week stays `/`."""
    monkeypatch.setattr(api.espn_season, "week_state", lambda week=None, team=None, **k: {
        "week": week or 6, "live": False, "me": {"team": "t", "team_id": team or 7, "abbrev": "L"},
        "opp": {"team": "x", "team_id": 5, "abbrev": "R"}, "mine": team in (None, 7)})
    monkeypatch.setattr(api.app.state, "runner", _lane_runner(week=6, store=_store([{"delta": 6.0, "team_id": 7}])),
                        raising=False)
    monkeypatch.setattr(api.db, "plays_rows", lambda week, **k: [{"delta": 2.0, "team_id": 7, "week": week}])
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
    st.update({HOME_KEY: ({1: 24.7}, 2)}, {"DET": {"status": "post"}}, {1: {"3": 200.0}})
    monkeypatch.setattr(api.live, "STATE", st)
    card = client.get("/roster").json()["cards"][0]
    assert card.get("actual") in (None, 0) and "game" not in card and not card.get("stat_line")
    st.update({HOME_KEY: ({1: 24.7}, 3)}, {"DET": {"status": "post"}}, {1: {"3": 200.0}})
    card = client.get("/roster").json()["cards"][0]
    assert card["game"]["status"] == "post"


def test_card_outlook_is_rewritten_on_a_post_and_polled_on_a_get(client, monkeypatch):
    import threading
    gate = threading.Event()
    take = {"label": "Take", "text": "He starts.", "sources": [{"site": "Rotowire", "date": "10/1", "url": "https://x.test"}]}
    monkeypatch.setattr(api.outlook, "run_claude", lambda prompt, **kw: (gate.wait(5), [take])[1])
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
    monkeypatch.setattr(api.espn_season, "player_card", lambda pid, **k: {**PLAYER_LOG, "player_id": pid})
    r = client.get("/player/7")
    assert r.status_code == 200 and r.json()["player_id"] == 7 and r.json()["stale_since"] is None


def test_player_502(client, monkeypatch):
    def boom(pid, **k):
        raise RuntimeError("espn down")
    monkeypatch.setattr(api.espn_season, "player_card", boom)
    assert client.get("/player/8").status_code == 502


def test_player_live_week(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "player_card", lambda pid, **k: {**PLAYER_LOG, "player_id": pid})
    monkeypatch.setattr(api.live.STATE, "stats", {9: {"0": 10, "1": 7, "3": 80}})
    monkeypatch.setattr(api.live.STATE, "leagues", {HOME_KEY: ({9: 3.2}, 4)})
    monkeypatch.setattr(api.live.STATE, "games", {"BUF": {"status": "in"}})
    monkeypatch.setattr(api, "_scoring", lambda *, ref: [{"stat_id": 3, "label": "Passing yards", "points": 0.04}])
    weeks = client.get("/player/9").json()["seasons"][0]["weeks"]
    assert [(w["week"], w["status"], w["pts"]) for w in weeks] == [(4, "live", 3.2)]


def test_players_search_spans_every_position(client, monkeypatch):
    rb = _fa_pool(monkeypatch)
    wr = {**rb, "player_id": 8, "name": "Bo Nix", "pos": "WR", "team": "DEN", "add": 12.0}
    qb = {**rb, "player_id": 9, "name": "Cam Ward", "pos": "QB", "team": "TEN", "add": 3.0}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None, **k: {"week": 3, "positions": {"RB": [rb], "WR": [wr], "QB": [qb]}})
    api.cache_for(home_league()).clear()
    body = client.get("/players?q=b").json()
    assert [r["player_id"] for r in body["rows"]] == [8, 7], "every pool, by add, best first"
    assert body["pos"] is None and body["q"] == "b" and all("has_outlook" in r for r in body["rows"])
    assert [r["player_id"] for r in client.get("/players?q=ten&pos=RB").json()["rows"]] == [9], "q ignores pos"
    assert client.get("/players").json()["view"]["preset"] == "waivers", "no pos and no query: the default preset"
    assert client.get("/players?q=%20&pos=RB").json()["pos"] == "RB", "a blank query is no query"


@pytest.fixture
def claims_env(client, monkeypatch):
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    pool = {"WR": [{"player_id": 101, "name": "Addison", "status": "WAIVERS"}, {"player_id": 102, "name": "Mims", "status": "WAIVERS"}],
            "D/ST": [{"player_id": -16020, "name": "Vikings D/ST", "status": "WAIVERS"}]}
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None, **k: {"week": 6, "roster": roster, "positions": pool})
    # reads the current pool dict on every call, so a test can flip a status between load and Submit
    monkeypatch.setattr(api.espn_season, "pool_status", lambda **k: {r["player_id"]: {"status": r["status"], "waiver_date": None} for rows in pool.values() for r in rows})
    monkeypatch.setattr(api, "_roster_size", lambda **k: 17)
    monkeypatch.setattr(api, "_limits", lambda **k: {})
    api.espn_write._fake(home_league()).claims.clear()
    api.espn_write._fake(home_league()).adds.clear()
    api.espn_write._fake(home_league()).fail_adds.clear()
    api.cache_for(home_league()).clear()
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


def test_a_target_another_team_took_keeps_his_name_and_says_who(claims_env, monkeypatch):
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    taken = {"player_id": 102, "name": "Mims", "pos": "WR", "team": "DEN", "owner": {"team_id": 3, "name": "Hawks", "abbrev": "HWK"}, "mine": False}
    pool = {"WR": [claims_env.pool["WR"][0]]}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None, **k: {"week": 6, "roster": roster, "positions": pool, "rostered": {"WR": [taken]}})
    r = claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]}).json()
    assert r["players"]["102"]["name"] == "Mims"
    assert r["players"]["102"]["droppable"] is None, "not offered as a cut: the panel reads droppable as mine"
    assert [e["reason"] for e in r["errors"] if e["player_id"] == 102] == ["taken by Hawks; remove him from the group"]


def test_submit_happy_path_and_decision(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["match"] and len(r["sent"]) == 4 and r["now"] == []
    g = claims_env.get("/claims").json()
    assert g["edited_since_submit"] is False and all(p["from_app"] for p in g["pending"])
    assert _titles() == ["Week 6 waiver claims"]
    assert r["decisions"] == [{"id": db.decisions(ref=home_league())[0]["id"], "needs_rationale": True}]


def test_a_resubmit_updates_the_weeks_claims_row_and_keeps_its_rationale(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    first = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"][0]
    db.update_decision(first["id"], {"rationale": ["best_ros"]}, ref=home_league())
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [1], "open": 0}]})
    again = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"]
    assert again == [{"id": first["id"], "needs_rationale": False}]
    assert len(db.decisions(ref=home_league())) == 1 and "Mims" in db.decisions(ref=home_league())[0]["decision"]


def test_a_dev_api_submit_records_no_decision(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    assert claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"] == []
    assert db.decisions(ref=home_league()) == []


def test_a_test_server_records_a_fake_claim_only_when_asked(claims_env, monkeypatch):
    # FFL_WRITES=0 (conftest): the claims go to the in-memory fake, the page says so, and nothing is recorded
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    assert claims_env.get("/claims").json()["writes"] is False
    assert claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"] == []
    assert db.decisions(ref=home_league()) == [] and api.espn_write._fake(home_league()).claims, "the fake took the claim; the decisions table did not"
    # FFL_RECORD_FAKE=1 (a worktree test server, own DB): recorded, marked fake, as /lineup and sit do
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]}).json()
    rows = {row["id"]: row for row in db.decisions(ref=home_league())}
    assert [rows[d["id"]]["title"] for d in r["decisions"]] == ["[fake] Week 6 waiver claims", "[fake] Week 6 add: Mims, drop R1"]
    assert all(row["facts"]["fake"] is True for row in rows.values())


def test_claims_say_when_writes_are_real(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    assert claims_env.get("/claims").json()["writes"] is True


def test_an_empty_plan_withdraws_the_claims_sent_from_here(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    outside = api.espn_write.submit_claim(-16020, 3, 6, ref=home_league())                 # filed on ESPN's site, not here
    claims_env.post("/claims", json={"groups": []})
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["sent"] == [] and r["match"] and [c["id"] for c in r["pending"]] == [outside], "ours gone, the outside one kept"
    g = claims_env.get("/claims").json()
    assert g["last_submit"]["claims"] == [] and g["last_submit"]["ids"] == [] and g["errors"] == []
    assert [c["from_app"] for c in g["pending"]] == [False]
    r = claims_env.post("/claims/submit", json={"replace_outside": True}).json()
    assert r["pending"] == [] and api.espn_write._fake(home_league()).claims == [], "the box ticked: the outside claim goes too"


def test_an_undesignated_ir_player_blocks_the_submit(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    api.cache_for(home_league()).clear()
    pool_fn = api.espn_season.free_agent_pool

    def with_bad_ir(week=None, **k):
        d = pool_fn(week, **k)
        d["roster"] = d["roster"] + [{"player_id": 99, "slot": "IR", "name": "Stuck", "pos": "RB", "droppable": True, "locked": False, "injury": "ACTIVE"}]
        return d

    monkeypatch.setattr(api.espn_season, "free_agent_pool", with_bad_ir)
    g = claims_env.get("/claims").json()
    assert g["errors"] == [{"group": None, "player_id": 99, "reason": api.claims_mod.IR_BLOCK}]
    assert g["players"]["99"]["name"] == "Stuck", "the panel can name him"
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["player_id"] == 99
    assert api.espn_write._fake(home_league()).claims == [], "nothing reached the writer"
    # a fake lineup move (FFL_WRITES=0) took him off IR: the claims read the fake's slots, as the Roster page does
    monkeypatch.setattr(api.espn_write._fake(home_league()), "slots", {99: "BE"})
    assert claims_env.get("/claims").json()["errors"] == []
    assert claims_env.post("/claims/submit", json={"replace_outside": False}).status_code == 200


def test_a_lineup_move_rebuilds_the_claims_roster(lineup_env, monkeypatch):
    builds = {"n": 0}

    def pool(week=None, **k):
        builds["n"] += 1
        return {"week": 3, "roster": [], "positions": {"WR": []}}

    monkeypatch.setattr(api.espn_season, "free_agent_pool", pool)
    monkeypatch.setattr(api, "_roster_size", lambda **k: 17)
    monkeypatch.setattr(api, "_limits", lambda **k: {})
    lineup_env.get("/claims")
    lineup_env.get("/claims")
    assert builds["n"] == 1
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    lineup_env.get("/claims")
    assert builds["n"] == 2, "the move off IR the panel asked for must clear the block on the next read"


def _counting_board(monkeypatch):
    """week:current built from a counter: the version a read sees says whether it was built after the move."""
    builds = {"n": 0}

    def week_state(week=None, team=None, **k):
        builds["n"] += 1
        return {"week": 3, "v": builds["n"]}

    monkeypatch.setattr(api.espn_season, "week_state", week_state)
    assert api._board(None, None, ref=home_league())["v"] == 1
    return builds


def test_a_lineup_move_shows_on_the_matchup_board_at_once(lineup_env, monkeypatch):
    _counting_board(monkeypatch)
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    assert api._board(None, None, ref=home_league())["v"] == 2, "the first open after the move is the moved lineup, never the old board"


def test_waiver_run_retires_the_submitted_plan(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    api.espn_write._fake(home_league()).claims.clear()                               # the run processed the claims
    monkeypatch.setattr(api.espn_write._fake(home_league()), "last_run", "2999-01-01T00:00:00")
    g = claims_env.get("/claims").json()
    assert g["groups"] == [] and g["last_submit"] is None and g["errors"] == []
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [2], "open": 0}]})
    saved = db.claim_plan(6, ref=home_league())
    assert saved["submits"] == [] and saved["retired"][0]["groups"][0]["targets"] == [101]
    assert claims_env.get("/claims").json()["groups"][0]["targets"] == [102], "a plan saved after the run stays"


def test_a_plan_after_the_waiver_run_is_a_new_claims_decision(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    ran = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"][0]
    db.update_decision(ran["id"], {"rationale": ["best_ros"]}, ref=home_league())
    api.espn_write._fake(home_league()).claims.clear()                               # the run processed the claims
    monkeypatch.setattr(api.espn_write._fake(home_league()), "last_run", "2999-01-01T00:00:00")
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [2], "open": 0}]})
    fresh = claims_env.post("/claims/submit", json={"replace_outside": False}).json()["decisions"]
    assert fresh[0]["id"] != ran["id"] and fresh[0]["needs_rationale"] is True
    rows = {r["id"]: r for r in db.decisions(ref=home_league())}
    assert "Addison" in rows[ran["id"]]["decision"] and rows[ran["id"]]["rationale"] == ["best_ros"]
    assert "Mims" in rows[fresh[0]["id"]]["decision"] and rows[fresh[0]["id"]]["facts"]["gen"] == 1


def test_submit_free_agent_is_instant(claims_env):
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"          # Mims cleared waivers
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]}).json()
    assert r["now"] == [{"group": 0, "add": 102, "drop": 1}] and [(c["add"], c["drop"]) for c in r["sent"]] == [(101, 2)]
    assert api.espn_write._fake(home_league()).adds == [{"add": 102, "drop": 1}]


def test_instant_add_rebuilds_only_the_roster_pages_inline(claims_env):
    built = {"week:current": 0, "player:9": 0}

    def builder(key):
        def fn():
            built[key] += 1
            return {}
        return fn
    for key in built:
        api.cache_for(home_league()).get_or_build(key, builder(key))
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [102], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert built == {"week:current": 2, "player:9": 1}         # the player card waits for its next request
    assert api.cache_for(home_league())._d["player:9"].expires == 0.0


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
    assert api.espn_write._fake(home_league()).claims == [] and api.espn_write._fake(home_league()).adds == []


def test_instant_adds_gate_off_rejects_free_agent_targets(claims_env, monkeypatch):
    monkeypatch.setenv("FFL_INSTANT_ADDS", "0")
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    assert api.claims_mod.INSTANT_OFF in [e["reason"] for e in claims_env.get("/claims").json()["errors"]]
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 422 and "FFL_INSTANT_ADDS" in str(r.json())
    assert api.espn_write._fake(home_league()).claims == [] and api.espn_write._fake(home_league()).adds == []
    monkeypatch.delenv("FFL_INSTANT_ADDS")                     # unset is on
    assert claims_env.get("/claims").json()["errors"] == []


def test_submit_espn_week_changed_is_409(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write._fake(home_league()), "period", 7)     # the pool is cached at week 6
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 409 and r.json()["detail"] == {"error": "ESPN's week changed — reload"}
    assert api.espn_write._fake(home_league()).claims == [] and api.espn_write._fake(home_league()).adds == []
    assert not api.cache_for(home_league()).has("waivers")                        # the reload builds the new week's pool


def test_submit_writes_carry_espn_period(claims_env, monkeypatch):
    monkeypatch.setattr(api.espn_write._fake(home_league()), "period", 6)
    weeks = []
    real_submit, real_add = api.espn_write.submit_claim, api.espn_write.add_free_agent
    monkeypatch.setattr(api.espn_write, "submit_claim", lambda a, d, wk, **k: weeks.append(wk) or real_submit(a, d, wk, **k))
    monkeypatch.setattr(api.espn_write, "add_free_agent", lambda a, d, wk, **k: weeks.append(wk) or real_add(a, d, wk, **k))
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 200 and weeks == [6, 6]


def test_landed_add_rebuilds_the_pool_before_returning(claims_env, monkeypatch):
    calls = {"n": 0}
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]

    def pool_fn(week=None, **k):
        calls["n"] += 1
        r = roster
        if api.espn_write._fake(home_league()).adds:                          # ESPN now has the add: Mims on, R1 off
            r = [p for p in roster if p["player_id"] != 1] + [{**roster[0], "player_id": 102, "name": "Mims"}]
        return {"week": 6, "roster": r, "positions": claims_env.pool}

    monkeypatch.setattr(api.espn_season, "free_agent_pool", pool_fn)
    api.cache_for(home_league()).clear()
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
    api.cache_for(home_league()).clear()
    pool_fn = api.espn_season.free_agent_pool

    def with_undroppable(week=None, **k):
        d = pool_fn(week, **k)
        d["roster"] = [{**p, "droppable": p["player_id"] != 3} for p in d["roster"]]
        return d

    monkeypatch.setattr(api.espn_season, "free_agent_pool", with_undroppable)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 422 and "undroppable" in str(r.json())


def test_submit_expired_login(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})

    def boom(max_age=0, **k):
        raise api.espn_write.WriteError(401, "expired")

    monkeypatch.setattr(api.espn_write, "pending_state", boom)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 502 and r.json()["detail"]["error"] == api.espn_write.EXPIRED


def test_unreachable_espn_is_not_a_login_expired(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})

    def boom(max_age=0, **k):
        raise api.espn_write.WriteError(0, "ReadTimeout: read timed out")

    monkeypatch.setattr(api.espn_write, "pending_state", boom)
    r = claims_env.post("/claims/submit", json={"replace_outside": False})
    assert r.status_code == 502 and r.json()["detail"]["error"] == api.espn_write.UNREACHABLE
    assert claims_env.get("/claims").json()["pending_error"] == api.espn_write.UNREACHABLE
    monkeypatch.setattr(api.espn_write, "pending_state", lambda max_age=0, **k: (_ for _ in ()).throw(api.espn_write.WriteError(403, "x")))
    assert claims_env.get("/claims").json()["pending_error"] == api.espn_write.EXPIRED


def test_claims_page_reuses_the_pending_read_but_fresh_and_submit_read_once(claims_env, monkeypatch):
    calls = []
    monkeypatch.setattr(api.espn_write, "pending_state", lambda max_age=0, **k: calls.append(("pending", max_age)) or ([], None))
    monkeypatch.setattr(api.espn_write, "last_waiver_run", lambda max_age=0, **k: calls.append(("run", max_age)))
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    calls.clear()  # the first save had no plan to retire
    claims_env.get("/claims")
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    reuse = api.CLAIMS_REUSE_S
    assert calls == [("run", reuse), ("pending", reuse)] * 2
    calls.clear()
    claims_env.get("/claims?fresh=1")
    claims_env.post("/claims/submit", json={"replace_outside": False})
    # the retire check reads ESPN anew; the pending list takes that read
    assert calls[:4] == [("run", 0), ("pending", api.JUST_READ_S)] * 2


def _roll_week(claims_env, monkeypatch):
    """ESPN's week moves on to 7, same roster and pool."""
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False} for i in range(1, 17)]
    pool = claims_env.pool
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None, **k: {"week": 7, "roster": roster, "positions": pool})
    api.cache_for(home_league()).clear()


def test_a_plan_carries_over_the_week_rollover(claims_env, monkeypatch):
    # ESPN rolls its week on Tuesday, before the Wednesday run: Monday's plan is still the plan
    g = [{"targets": [101], "cuts": [1], "open": 0}]
    claims_env.post("/claims", json={"groups": g})
    _roll_week(claims_env, monkeypatch)
    r = claims_env.get("/claims").json()
    assert r["week"] == 7 and r["groups"] == g
    assert db.claim_plan(6, ref=home_league())["groups"] == g, "last week's row is untouched"
    claims_env.post("/claims", json={"groups": g + [{"targets": [102], "cuts": [2], "open": 0}]})
    assert db.claim_plan(7, ref=home_league())["week"] == 7 and len(db.claim_plan(7, ref=home_league())["groups"]) == 2, "a save writes the new week's row"


def test_a_sent_plan_carries_over_with_its_submit(claims_env, monkeypatch):
    # claims sent before the rollover are still on ESPN: the next submit must know their ids to replace them
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    _roll_week(claims_env, monkeypatch)
    r = claims_env.get("/claims").json()
    assert r["groups"][0]["targets"] == [101] and r["last_submit"] and r["on_espn"] is True


def test_a_processed_plan_does_not_carry_over(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    api.espn_write._fake(home_league()).claims.clear()                               # the run processed the claims
    monkeypatch.setattr(api.espn_write._fake(home_league()), "last_run", "2999-01-01T00:00:00")
    _roll_week(claims_env, monkeypatch)
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

    def down(**k):   # the read-back only: the pre-submit read is pending_state
        raise api.espn_write.WriteError(503, "down")

    monkeypatch.setattr(api.espn_write, "pending_claims", down)
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    assert r.status_code == 200 and r.json()["error"] and r.json()["match"] is False
    plan = db.claim_plan(6, ref=home_league())
    assert len(plan["submits"]) == 1 and plan["submits"][0]["now"] == [{"group": 0, "add": 102, "drop": 1}]
    assert db.decisions(ref=home_league()), "the landed add is recorded"


def test_failed_cancel_keeps_app_claim_tracked(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    old = [p["id"] for p in claims_env.get("/claims").json()["pending"]]
    real, calls = api.espn_write.cancel_claim, {"n": 0}

    def flaky(i, wk, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise api.espn_write.WriteError(500, "boom")
        return real(i, wk, **k)

    monkeypatch.setattr(api.espn_write, "cancel_claim", flaky)
    r = claims_env.post("/claims/submit", json={"replace_outside": False}).json()
    assert r["error"] and r["sent"] == []
    plan = db.claim_plan(6, ref=home_league())
    assert set(plan["submits"][-1]["ids"]) == set(old) - {old[0]}   # the first was cancelled, the rest still held
    assert old[1] in plan["submits"][-1]["ids"]
    pend = claims_env.get("/claims").json()["pending"]
    assert pend and all(p["from_app"] for p in pend)


def test_save_during_submit_is_409(claims_env):
    assert api._lock("submit", home_league()).acquire(blocking=False)
    try:
        r = claims_env.post("/claims", json={"groups": []})
    finally:
        api._lock("submit", home_league()).release()
    assert r.status_code == 409
    assert claims_env.post("/claims", json={"groups": []}).status_code == 200   # released


def test_a_submits_answer_is_kept_under_its_token(claims_env):
    # the phone lost the POST (left the app mid-send): the dialog collects the answer the server finished with
    api._SUBMITS.clear()
    assert claims_env.get("/claims/submit/t1").status_code == 404, "never arrived"
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    r = claims_env.post("/claims/submit", json={"replace_outside": False, "token": "t1"}).json()
    got = claims_env.get("/claims/submit/t1").json()
    assert got == {"state": "done", "status": 200, "body": r} and r["match"]
    # a refusal is kept with its status and detail, as the POST answered it
    claims_env.pool["WR"][0]["status"] = "FREEAGENT"
    assert claims_env.post("/claims/submit", json={"replace_outside": False, "token": "t2"}).status_code == 409
    got = claims_env.get("/claims/submit/t2").json()
    assert got["status"] == 409 and got["body"]["detail"]["error"] == "statuses changed — review again"


def test_a_running_submit_reads_as_running(claims_env):
    api._SUBMITS.clear()
    api._keep_submit("t", None, ref=home_league())
    assert claims_env.get("/claims/submit/t").json() == {"state": "running"}
    for i in range(api._SUBMITS_KEPT):
        api._keep_submit(f"x{i}", {"status": 200, "body": {}}, ref=home_league())
    assert claims_env.get("/claims/submit/t").status_code == 404, "only the latest few are kept"


def test_instant_add_is_consumed_from_the_plan(claims_env, monkeypatch):
    claims_env.pool["WR"][1]["status"] = "FREEAGENT"
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1, 2], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False, "expect_now": [{"group": 0, "add": 102, "drop": 1}]})
    roster = [{"player_id": i, "slot": "BE", "name": f"R{i}", "pos": "WR", "droppable": True, "locked": False}
              for i in range(2, 17)] + [{"player_id": 102, "slot": "BE", "name": "Mims", "pos": "WR", "droppable": True, "locked": False}]
    pool = {"WR": [claims_env.pool["WR"][0]]}   # Mims left the pool, R1 left the roster
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None, **k: {"week": 6, "roster": roster, "positions": pool})
    api.cache_for(home_league()).clear()
    r = claims_env.get("/claims").json()
    assert r["groups"] == [{"targets": [101], "cuts": [2], "open": 0}]
    assert r["errors"] == [] and r["edited_since_submit"] is False


def test_on_espn_checks_espn_not_the_record(claims_env):
    assert claims_env.get("/claims").json()["on_espn"] is False             # never sent
    claims_env.post("/claims", json={"groups": [{"targets": [101, 102], "cuts": [1], "open": 1}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    plan = db.claim_plan(6, ref=home_league())
    assert plan["submits"][-1]["match"] is True
    assert claims_env.get("/claims").json()["on_espn"] is True
    api.espn_write._fake(home_league()).claims.pop(1)                                             # one cancelled on ESPN's site
    g = claims_env.get("/claims").json()
    assert g["on_espn"] is False and g["edited_since_submit"] is False


def test_on_espn_ignores_kept_outside_claims(claims_env):
    api.espn_write._fake(home_league()).claims.append({"id": "outside-1", "add": 201, "drop": 5})  # filed on ESPN directly
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    claims_env.post("/claims/submit", json={"replace_outside": False})
    assert claims_env.get("/claims").json()["on_espn"] is True


def test_target_on_my_roster_is_an_error(claims_env):
    claims_env.post("/claims", json={"groups": [{"targets": [5], "cuts": [1], "open": 0}]})
    assert "already on your roster" in [e["reason"] for e in claims_env.get("/claims").json()["errors"]]


def test_save_plan_failure_keeps_the_old_plan(claims_env, monkeypatch):
    claims_env.post("/claims", json={"groups": [{"targets": [101], "cuts": [1], "open": 0}]})
    good = db.claim_plan(6, ref=home_league())

    def crash(*a, **k):
        raise db.DBError("disk full")

    monkeypatch.setattr(db, "_upsert", crash)
    with pytest.raises(db.DBError):
        api._save_plan({**good, "groups": []}, ref=home_league())
    assert db.claim_plan(6, ref=home_league()) == good


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
    monkeypatch.setattr(api.espn_season, "free_agent_pool", lambda week=None, **k: pool)
    monkeypatch.setattr(api.outlook, "_WRITING", {})
    api.cache_for(home_league()).clear()
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
                        lambda week=None, **k: {"week": 3, "positions": {"RB": [{"player_id": 7, "name": "B", "pos": "RB", "team": "MIA", "add": 1.0}]}})
    api.cache_for(home_league()).clear()
    assert [r["player_id"] for r in client.get("/players?pos=RB&fa=0").json()["rows"]] == [7]


def test_claims_never_see_a_rostered_player(monkeypatch):
    pool = _league_pool(monkeypatch)
    assert set(api._pool_rows(pool)) == {7}


@pytest.fixture
def lineup_env(client, monkeypatch):
    cards = [{"player_id": 3, "name": "Bench", "pos": "RB", "slot": "BE", "call": "START", "gap": 1.4, "group": "bench", "locked": False},
             {"player_id": 2, "name": "Flex", "pos": "WR", "slot": "RB/WR/TE", "call": "BENCH", "gap": -1.4, "group": "starter", "locked": False}]
    state = {"slots": {3: "BE", 2: "RB/WR/TE"}, "locked": set(), "period": 3, "builds": 0, "flag_reads": 0}

    def roster_state(week=None, flags=None, **k):
        state["builds"] += 1
        state["readback_flags"] = flags
        return {"week": 3, "live": False, "empty_slots": [], "writes": False,
                "cards": [{**c, "slot": state["slots"][c["player_id"]]} for c in cards]}
    monkeypatch.setattr(api.espn_season, "roster_state", roster_state)
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda *a, **k: None)
    def roster_flags(lg, **k):
        state["flag_reads"] += 1
        return {i: {"locked": i in state["locked"], "slot": s} for i, s in state["slots"].items()}
    monkeypatch.setattr(api.espn_season, "roster_flags", roster_flags)
    monkeypatch.setattr(api.espn_write, "writes", lambda: True)  # these model real writes (move_lineup is stubbed)
    monkeypatch.setattr(api.espn_season, "load", lambda *a, **k: {"starters": {"RB/WR/TE": 1}})
    monkeypatch.setattr(api.espn_write, "pending_state", lambda max_age=0, **k: ([], state["period"]))
    sent = []
    monkeypatch.setattr(api.espn_write, "move_lineup",
                        lambda items, week, **k: sent.append((items, week)) or state["slots"].update({p: t for p, _, t in items}))
    client.state, client.sent = state, sent
    return client


ORIGINAL_WRITES = api.espn_write.writes
SWAP = {"moves": [{"player_id": 3, "to_slot": "RB/WR/TE"}, {"player_id": 2, "to_slot": "BE"}]}
ORIGINAL_MOVE_LINEUP = api.espn_write.move_lineup


def _titles():
    return [r["title"] for r in db.decisions(ref=home_league())]


def test_lineup_swap_writes_once_logs_it_and_returns_the_fresh_roster(lineup_env):
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200
    assert lineup_env.sent == [([(3, "BE", "RB/WR/TE"), (2, "RB/WR/TE", "BE")], 3)]
    assert {c["player_id"]: c["slot"] for c in r.json()["cards"]} == {3: "RB/WR/TE", 2: "BE"}, "read back, not echoed"
    assert _titles() == ["Week 3 lineup: Bench to FLEX, Flex to the bench"]
    assert r.json()["decision"] == {"id": db.decisions(ref=home_league())[0]["id"], "needs_rationale": False}, "Bench START in, Flex BENCH out: the call"


def test_a_decision_the_db_cannot_take_never_fails_the_move(lineup_env, monkeypatch, caplog):
    def broken(row, season=None, **k):
        raise db.sa.exc.OperationalError("insert", {}, Exception("disk I/O error"))
    monkeypatch.setattr(api.db, "add_decision", broken)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is None and "not recorded" in caplog.text


def test_a_test_server_can_record_fake_moves(lineup_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    monkeypatch.setenv("FFL_RECORD_FAKE", "1")
    r = lineup_env.post("/lineup", json=SWAP)
    row = db.decisions(ref=home_league())[0]
    assert row["title"].startswith("[fake] Week 3 lineup") and row["facts"]["fake"] is True and r.json()["decision"]["id"] == row["id"]


def test_logging_off_in_settings_records_no_move(lineup_env):
    assert lineup_env.get("/decisions/prefs").json() == {"log": True, "page": True}
    assert lineup_env.post("/decisions/prefs", json={"log": False}).json() == {"log": False, "page": True}
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is None and db.decisions(ref=home_league()) == []
    assert lineup_env.post("/decisions/prefs", json={"page": "no"}).status_code == 422
    assert lineup_env.get("/decisions/prefs").json() == {"log": False, "page": True}, "a refused change leaves the switches"


def test_turning_the_decisions_page_off_turns_logging_off(lineup_env):
    assert lineup_env.post("/decisions/prefs", json={"page": False}).json() == {"log": False, "page": False}
    assert lineup_env.post("/decisions/prefs", json={"log": True}).json() == {"log": False, "page": False}, "no logging without the page"
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is None and db.decisions(ref=home_league()) == []
    assert lineup_env.post("/decisions/prefs", json={"page": True, "log": True}).json() == {"log": True, "page": True}


def test_unreadable_decision_prefs_never_fail_a_move(lineup_env, monkeypatch, caplog):
    def broken(key, default=None, **k):
        raise db.sa.exc.OperationalError("select", {}, Exception("disk I/O error"))
    monkeypatch.setattr(api.db, "get_league_state", broken)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 200 and r.json()["decision"] is not None and "defaults used" in caplog.text


def test_lineup_keeps_the_row_the_moved_player_took(lineup_env):
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    assert db.seat_map(ref=home_league()) == {3: 2}, "Bench stands in Flex's place in ESPN's order; Flex went to the bench"


def test_lineup_fake_writes_validate_the_second_swap_against_the_first(lineup_env, monkeypatch):
    # FFL_WRITES=0: ESPN never moves, the fake store does; roster_state shows the fake slots, and so must the validator
    monkeypatch.setenv("FFL_WRITES", "0")
    monkeypatch.setattr(api.espn_write._fake(home_league()), "slots", {})
    monkeypatch.setattr(api.espn_write, "move_lineup", ORIGINAL_MOVE_LINEUP)
    monkeypatch.setattr(api.espn_write, "writes", ORIGINAL_WRITES)
    real = api.espn_season.roster_state
    monkeypatch.setattr(api.espn_season, "roster_state", lambda week=None, flags=None, **k: {
        **(d := real(week)), "cards": [{**c, "slot": api.espn_write.fake_slots(ref=home_league()).get(c["player_id"], c["slot"])} for c in d["cards"]]})
    assert lineup_env.post("/lineup", json=SWAP).status_code == 200
    back = {"moves": [{"player_id": 3, "to_slot": "BE"}, {"player_id": 2, "to_slot": "RB/WR/TE"}]}
    r = lineup_env.post("/lineup", json=back)
    assert r.status_code == 200
    assert api.espn_write.fake_slots(ref=home_league()) == {3: "BE", 2: "RB/WR/TE"}, "the second move ran from the first one's slots"
    assert {c["player_id"]: c["slot"] for c in r.json()["cards"]} == {3: "BE", 2: "RB/WR/TE"}
    assert db.decisions(ref=home_league()) == [], "a fake move is not a decision: logged, never journaled"


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
    assert db.decisions(ref=home_league()) == []
    assert "Week 3 lineup: Bench to FLEX" in caplog.text


def test_lineup_maps_errors_from_the_concurrent_reads_as_before(lineup_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "pending_state", lambda **k: (_ for _ in ()).throw(api.espn_write.WriteError(401, "x")))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["error"] == api.espn_write.EXPIRED
    monkeypatch.setattr(api.espn_write, "pending_state", lambda **k: (_ for _ in ()).throw(api.espn_write.WriteError(0, "Timeout: t")))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["error"] == "ESPN couldn't be reached, try again"
    monkeypatch.setattr(api.espn_season, "roster_flags", lambda lg, **k: (_ for _ in ()).throw(RuntimeError("mRoster down")))
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
    assert api._lock("lineup", home_league()).acquire(blocking=False)
    try:
        assert lineup_env.post("/lineup", json=SWAP).status_code == 409
    finally:
        api._lock("lineup", home_league()).release()


def test_lineup_reports_espns_refusal(lineup_env, monkeypatch):
    def boom(items, week, **k):
        raise api.espn_write.WriteError(401, "nope")
    monkeypatch.setattr(api.espn_write, "move_lineup", boom)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["error"] == api.espn_write.EXPIRED


def test_lineup_read_back_failure_says_it_moved(lineup_env, monkeypatch):
    real = api.espn_season.roster_state
    def after_write(week=None, flags=None, **k):
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
                        lambda week=None, flags=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")) if lineup_env.sent else real(week))
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["moved"] is True and len(lineup_env.sent) == 1
    assert _titles() == ["Week 3 lineup: Bench to FLEX, Flex to the bench"]


def test_lineup_timeout_may_have_moved_so_it_says_so_journals_it_and_drops_the_page(lineup_env, monkeypatch):
    def slow(items, week, **k):
        raise api.requests.Timeout("15s")
    lineup_env.get("/roster")
    monkeypatch.setattr(api.espn_write, "move_lineup", slow)
    r = lineup_env.post("/lineup", json=SWAP)
    assert r.status_code == 502 and r.json()["detail"]["moved"] is True and "did not answer" in r.json()["detail"]["error"]
    assert db.decisions(ref=home_league())[0]["facts"]["uncertain"] is True
    assert not api.cache_for(home_league()).has("roster")


def test_lineup_posts_the_from_slots_espn_has_now_not_the_cached_ones(lineup_env):
    lineup_env.get("/roster")  # caches the page with 3 on the bench, 2 at FLEX
    lineup_env.state["slots"] = {3: "RB/WR/TE", 2: "BE"}  # moved on ESPN since
    r = lineup_env.post("/lineup", json={"moves": [{"player_id": 3, "to_slot": "BE"}, {"player_id": 2, "to_slot": "RB/WR/TE"}]})
    assert r.status_code == 200
    assert lineup_env.sent == [([(3, "RB/WR/TE", "BE"), (2, "BE", "RB/WR/TE")], 3)]


def test_cache_put_beats_a_revalidate_that_started_before_it():
    c = api.Cache(60.0, ref=home_league())
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
    monkeypatch.setattr(api.espn_write._fake(home_league()), "slots", {})
    monkeypatch.setattr(api.espn_write, "move_lineup", ORIGINAL_MOVE_LINEUP)
    monkeypatch.setattr(api.espn_write, "writes", ORIGINAL_WRITES)
    cards = [{"player_id": 5, "name": "Hurt", "pos": "WR", "slot": "IR", "injury": "INJURY_RESERVE", "call": "IR",
              "gap": None, "group": "ir", "locked": False},
             {"player_id": 7, "name": "Sore", "pos": "RB", "slot": "BE", "injury": "OUT", "call": "OUT",
              "gap": None, "group": "bench", "locked": False}]
    monkeypatch.setattr(api.espn_season, "roster_state", lambda week=None, flags=None, **k: {
        "week": 3, "live": False, "empty_slots": [], "writes": False,
        "cards": [{**c, "slot": api.espn_write.fake_slots(ref=home_league()).get(c["player_id"], c["slot"])} for c in cards]})
    monkeypatch.setattr(api.espn_season, "roster_flags",
                        lambda lg, **k: {c["player_id"]: {"locked": False, "slot": c["slot"]} for c in cards})
    monkeypatch.setattr(api.espn_season, "load", lambda *a, **k: {"starters": {"RB/WR/TE": 1}, "ir_size": 1})
    r = lineup_env.post("/lineup", json={"moves": [{"player_id": 5, "to_slot": "BE"}, {"player_id": 7, "to_slot": "IR"}]})
    assert r.status_code == 200, r.json()
    assert {c["player_id"]: c["slot"] for c in r.json()["cards"]} == {5: "BE", 7: "IR"}
    assert api.espn_write.fake_slots(ref=home_league()) == {5: "BE", 7: "IR"}
    # validated against the fake slots: IR is full now
    r = lineup_env.post("/lineup", json={"moves": [{"player_id": 5, "to_slot": "IR"}]})
    assert r.status_code == 409 and r.json()["detail"]["errors"] == ["IR: 2 players for 1 spots"]
    assert lineup_env.post("/lineup", json={"moves": [{"player_id": 7, "to_slot": "BE"}, {"player_id": 5, "to_slot": "IR"}]}).status_code == 200
    assert api.espn_write.fake_slots(ref=home_league()) == {5: "IR", 7: "BE"}
    assert db.decisions(ref=home_league()) == [], "a fake move is not a decision"


@pytest.fixture
def sit_env(client, monkeypatch):
    from ffl import season
    from tests.test_season import TWO_QB, roster
    state = {"builds": 0}

    def roster_state(week=None, flags=None, **k):
        state["builds"] += 1
        rows = api.espn_season.with_sits(roster(), db.sat_ids(3, ref=home_league()))
        return {"week": 3, "live": False, "empty_slots": [], "writes": False,
                "cards": season.roster_cards(rows, TWO_QB, 3)}
    monkeypatch.setattr(api.espn_season, "roster_state", roster_state)
    monkeypatch.setattr(api.espn_season, "load", lambda *a, **k: TWO_QB)
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
    assert db.sat_ids(3, ref=home_league()) == {3}
    assert _titles() == ["Week 3 sit: QB C"] and r.json()["decision"]["needs_rationale"] is True
    assert {c["player_id"]: c["call"] for c in sit_env.get("/roster").json()["cards"]}[3] == "SIT", "cached as sat"


def test_a_sit_shows_on_the_matchup_board_at_once(sit_env, monkeypatch):
    _counting_board(monkeypatch)
    assert sit_env.post("/roster/sit/3", json={"sat": True}).status_code == 200
    assert api._board(None, None, ref=home_league())["v"] == 2, "the first open after the sit is built without him"


def test_unsit_puts_him_back_and_a_repeat_is_not_journaled_twice(sit_env):
    sit_env.post("/roster/sit/3", json={"sat": True})
    assert sit_env.post("/roster/sit/3", json={"sat": True}).json()["decision"] is None
    r = sit_env.post("/roster/sit/3", json={"sat": False})
    assert {c["player_id"]: c["call"] for c in r.json()["cards"]}[3] == "START" and db.sat_ids(3, ref=home_league()) == set()
    assert sorted(_titles()) == ["Week 3 sit: QB C", "Week 3 unsit: QB C"]


def test_a_dev_api_sits_but_does_not_journal(sit_env, monkeypatch):
    monkeypatch.setattr(api.espn_write, "writes", lambda: False)
    assert sit_env.post("/roster/sit/3", json={"sat": True}).status_code == 200
    assert db.sat_ids(3, ref=home_league()) == {3} and db.decisions(ref=home_league()) == []


def test_sit_refuses_a_player_off_the_roster_and_a_running_move(sit_env):
    assert sit_env.post("/roster/sit/999", json={"sat": True}).status_code == 404
    api._lock("lineup", home_league()).acquire()
    try:
        assert sit_env.post("/roster/sit/3", json={"sat": True}).status_code == 409
    finally:
        api._lock("lineup", home_league()).release()
    assert db.sat_ids(3, ref=home_league()) == set()


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


ROSTER_KEYS = ["transactions", "waivers", "week:current", "week:5:3", "league:current", "team:6",
               "starters:current"]
OTHER_TEAMS_KEYS = ["roster", "team:3", "player:9"]  # my roster, another team's, a card of nobody in the move


def test_a_build_a_move_drops_partway_is_not_installed():
    """A page whose build began before a move read the rosters from before it: once the move drops the page, that
    build must not install itself (a first build or a revalidate), or the rewarm finds it fresh and serves it."""
    c = api.Cache(120, ref=home_league())

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


def test_a_page_stops_waiting_on_a_slow_first_build_and_the_build_goes_on():
    """A league just added reads its whole season on its first page: past the page's wait the GET answers 503
    `building` (the web says the league is still being imported) while the build carries on; the next GET joins that
    build rather than starting another, and once it lands the page is served from the cache."""
    c = api.Cache(120, ref=home_league())
    gate, calls = threading.Event(), []

    def slow():
        calls.append(1)
        gate.wait(5)
        return {"n": 1}
    with pytest.raises(api.Building) as e:
        c.get_or_build("roster", slow, wait=0.05)
    assert e.value.status_code == 503 and e.value.detail["error"] == "building"
    with pytest.raises(api.Building):
        c.get_or_build("roster", slow, wait=0.05)
    gate.set()
    assert c.get_or_build("roster", slow, wait=2)[0] == {"n": 1}
    assert calls == [1], "one build, however many GETs asked while it ran"
    assert c.get_or_build("roster", slow, wait=0.05)[0] == {"n": 1}


def test_a_first_build_that_fails_behind_is_the_pages_502():
    c = api.Cache(120, ref=home_league())

    def broken():
        raise RuntimeError("espn down")
    with pytest.raises(api.HTTPException) as e:
        c.get_or_build("roster", broken, wait=2)
    assert e.value.status_code == 502
    assert c.get_or_build("roster", lambda: {"n": 2}, wait=2)[0] == {"n": 2}, "a failed build leaves no build behind"


def test_every_rewarm_thread_is_joined():
    c = api.Cache(120, ref=home_league())
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
    for key in ROSTER_KEYS + OTHER_TEAMS_KEYS + ["player:5083754", "logo:3", "draft_picks"]:
        api.cache_for(home_league()).get_or_build(key, builder(key))
    monkeypatch.setattr(espn_season, "_BOXES", {("espn:100000001", 4): (math.inf, None, ["done"]), ("espn:100000001", 5): (9e18, None, ["now"])})
    monkeypatch.setattr(live, "STATE", live.State())

    journal.sync_transactions(_move_lg(), ref=home_league())
    api.cache_for(home_league()).join()

    assert all(built[k] == 2 for k in ROSTER_KEYS), built          # rebuilt behind the move
    assert built["player:5083754"] == 1 and not api.cache_for(home_league()).has("player:5083754")  # dropped, built on its next open
    assert all(built[k] == 1 and api.cache_for(home_league()).has(k) for k in OTHER_TEAMS_KEYS)  # team 6 moved
    assert built["logo:3"] == built["draft_picks"] == 1 and api.cache_for(home_league()).has("logo:3")
    assert list(espn_season._BOXES) == [("espn:100000001", 4)], "the current week's box scores are refetched, finished weeks kept"
    assert live.STATE.moves == 1 and live.STATE.version == 0

    journal.sync_transactions(_move_lg(), ref=home_league())  # nothing new: nothing rebuilt, no tick
    api.cache_for(home_league()).join()
    assert built["transactions"] == 2 and live.STATE.moves == 1


def test_notify_tick_409s_while_another_runs(client, monkeypatch):
    monkeypatch.setattr(api.notify, "tick", lambda: {"moves": 0})
    monkeypatch.setattr(api, "pregame_pull", lambda **k: [])
    monkeypatch.setattr(api, "correction_pass", lambda **k: {})
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
    return db.add_decision(row, ref=home_league())


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
    monkeypatch.setattr(api.espn_season, "transactions_state", lambda **k: {"moves": [], "teams": [], "stale_since": None})
    monkeypatch.setattr(api.espn_season, "draft_picks", lambda **k: [])
    api.cache_for(home_league()).clear()
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
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda *a, **k: "LG")
    monkeypatch.setattr(api.espn_season, "kickoffs_by_week", lambda lg, **k: {6: [kick, kick]})
    client.get("/week/3")
    assert client.post("/notify/tick").json()["leagues"][HOME_KEY]["pregame"] == [kick.isoformat()]
    api.cache_for(home_league()).join()
    assert client.calls["week"] == 2, "rebuilt behind the tick"
    assert client.post("/notify/tick").json()["leagues"][HOME_KEY]["pregame"] == [], "once per kickoff"
    monkeypatch.setattr(api.espn_season, "kickoffs_by_week", lambda lg, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    assert client.post("/notify/tick").json()["leagues"][HOME_KEY]["pregame"] == {"error": "espn down"}


def test_trace_stores_a_stamped_row(client):
    assert client.post("/trace", json={"event": "click", "fields": {"url": "/transactions"}}).json() == {"ok": True}
    assert client.post("/trace", json={"event": "hello"}).status_code == 200
    rows = db.trace_rows()
    assert [r["event"] for r in rows] == ["click", "hello"]
    assert rows[0]["url"] == "/transactions" and rows[0]["at"][:4].isdigit()
    assert not hasattr(api, "TRACE_PATH"), "nothing written beside the DB"


# --- page payloads in SQL (ffl-iq3): no cold load after a restart ---

def test_a_page_built_before_a_restart_is_served_at_once_then_rebuilt():
    api.Cache(120, persist=True, ref=home_league()).get_or_build("roster", lambda: {"n": 1})
    after = api.Cache(120, persist=True, ref=home_league())
    after.load()
    calls = []

    def build():
        calls.append(1)
        return {"n": 2}
    payload, stale = after.get_or_build("roster", build)
    assert payload == {"n": 1} and stale is None, "the stored page, at once, not flagged as a source failure"
    after.join()
    assert calls == [1] and after._d["roster"].payload == {"n": 2}
    assert [p["payload"] for p in db.pages(ref=home_league())] == [{"n": 2}], "the rebuild is written through"


def test_a_loaded_page_is_never_fresh():
    api.Cache(120, persist=True, ref=home_league()).get_or_build("league:current", lambda: {"n": 1})
    after = api.Cache(120, persist=True, ref=home_league())
    after.load()
    assert after._d["league:current"].expires == 0.0
    after.rebuild_expired()  # /refresh: a loaded page with no builder yet is left for its first request
    assert after._d["league:current"].payload == {"n": 1}


def test_only_pages_that_survive_json_are_stored():
    c = api.Cache(120, persist=True, ref=home_league())
    c.get_or_build("roster", lambda: {1: "int keys come back as strings"})
    c.get_or_build("waivers", lambda: {"pair": (1, 2)})
    c.get_or_build("starters:current", lambda: {"ids": [1]})
    c.get_or_build("logo:3", lambda: (b"png", "image/png"))
    c.get_or_build("draft_picks", lambda: {"picks": []})
    c.get_or_build("team:3", lambda: {"ok": True})
    assert [p["key"] for p in db.pages(ref=home_league())] == ["team:3"]


def test_a_dropped_page_never_comes_back_from_the_db():
    c = api.Cache(120, persist=True, ref=home_league())
    for k in ("roster", "team:3", "week:current", "league:current"):
        c.get_or_build(k, lambda: {"k": 1})
    c.drop_prefixes(("roster", "team"))
    c.drop("week:current")
    assert [p["key"] for p in db.pages(ref=home_league())] == ["league:current"]
    c.clear()
    assert db.pages(ref=home_league()) == []


def test_a_move_deletes_stored_pages_never_loaded():
    db.put_page("team:3", {"k": 1}, time.time(), ref=home_league())
    db.put_page("teams", {"k": 1}, time.time(), ref=home_league())  # another prefix that merely starts the same
    c = api.Cache(120, persist=True, ref=home_league())  # boot's load failed, say: nothing in memory
    assert c.drop_prefixes(("team",)) == []
    assert [p["key"] for p in db.pages(ref=home_league())] == ["teams"]


def test_a_build_a_move_overtakes_writes_no_row():
    c = api.Cache(120, persist=True, ref=home_league())

    def before_the_move():
        c.drop_prefixes(("roster",))
        return {"old": True}
    c.get_or_build("roster", before_the_move)
    assert db.pages(ref=home_league()) == []
    c.get_or_build("roster", lambda: {"n": 1})
    c._d["roster"].expires = 0.0
    c._d["roster"].fn = before_the_move
    c.get_or_build("roster", before_the_move)
    c.join()
    assert db.pages(ref=home_league()) == [], "the revalidate the move overtook leaves no row behind"


def test_put_writes_through():
    c = api.Cache(120, persist=True, ref=home_league())
    c.get_or_build("roster", lambda: {"n": 1})
    c.put("roster", {"n": 5})
    assert [p["payload"] for p in db.pages(ref=home_league())] == [{"n": 5}]


def test_old_player_cards_are_pruned_at_load():
    db.put_page("player:9", {"old": True}, time.time() - 8 * 86400, ref=home_league())
    db.put_page("player:8", {"new": True}, time.time(), ref=home_league())
    db.put_page("week:2", {"old": True}, time.time() - 8 * 86400, ref=home_league())
    c = api.Cache(120, persist=True, ref=home_league())
    c.load()
    assert sorted(c._d) == ["player:8", "week:2"]
    assert sorted(p["key"] for p in db.pages(ref=home_league())) == ["player:8", "week:2"]


def test_a_failed_write_keeps_the_page(monkeypatch):
    def broken(*a, **k):
        raise db.DBError("disk full")
    monkeypatch.setattr(api.db, "put_page", broken)
    c = api.Cache(120, persist=True, ref=home_league())
    assert c.get_or_build("roster", lambda: {"n": 1})[0] == {"n": 1}
    assert c.has("roster")


def test_startup_serves_the_stored_page_on_the_first_hit(client):
    db.put_page("week:3", {"week": 3, "live": False, "me": {"team": "stored"}, "opp": {"team": "opp"}}, time.time() - 3600, ref=home_league())
    with TestClient(api.app) as c:
        assert c.get("/week/3").json()["me"]["team"] == "stored"
        api.cache_for(home_league()).join()
        assert c.get("/week/3").json()["me"]["team"] == "me"


def test_a_cold_key_is_built_once_for_concurrent_callers():
    from tests.test_flight import park, race
    c, calls = api.Cache(60.0, ref=home_league()), []

    def build():
        park(c._flight, "k", 1)
        calls.append(1)
        return {"n": 1}

    assert race(2, lambda: c.get_or_build("k", build)) == [({"n": 1}, None)] * 2
    assert calls == [1] and c._d["k"].payload == {"n": 1}


def test_a_failed_shared_first_build_is_a_502_for_every_caller():
    from tests.test_flight import park, race
    c, calls = api.Cache(60.0, ref=home_league()), []

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
    c = api.Cache(60.0, ref=home_league())

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
    c, calls, release = api.Cache(60.0, ref=home_league()), [], threading.Event()

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
    monkeypatch.setattr(espn_season, "_BOXES", {("espn:100000001", 4): (math.inf, None, ["done"]), ("espn:100000001", 5): (9e18, None, ["now"])})
    monkeypatch.setattr(espn_season, "_GAP_GAMES", {("espn:100000001", 7, 3): [{"week": 3}]})
    api.expire_everything(ref=home_league())
    assert list(espn_season._BOXES) == [("espn:100000001", 4)] and ("espn:100000001", 7, 3) in espn_season._GAP_GAMES


# ffl-7sq: the stat-correction pass on the notify tick, and past boards' stored feeds
def _corr(week, pid=1, team=7):
    return {"at": "2026-10-14T12:00:00-04:00", "week": week, "player_id": pid, "team_id": team, "delta": 1.5,
            "total": 21.5, "text": "Stat correction: 20.0 → 21.5", "match": "correction", "quarter": None, "clock": None}


def _stub_correct(monkeypatch, out):
    monkeypatch.setattr(api.espn_season, "fresh_league", lambda *a, **k: "LG")
    monkeypatch.setattr(api.espn_season, "correct_boxes", lambda lg, now=None, **k: {**out, "plays": list(out.get("plays", []))})


def test_notify_tick_answers_with_the_corrections(client, monkeypatch):
    monkeypatch.setattr(api.notify, "tick", lambda: {"moves": 0})
    monkeypatch.setattr(api, "pregame_pull", lambda **k: [])
    _stub_correct(monkeypatch, {"weeks": {}, "changed": []})
    assert client.post("/notify/tick").json()["leagues"][HOME_KEY]["corrections"] == {"weeks": {}, "changed": []}
    monkeypatch.setattr(api.espn_season, "correct_boxes", lambda lg, now=None, **k: (_ for _ in ()).throw(RuntimeError("espn down")))
    r = client.post("/notify/tick")
    assert r.status_code == 200 and r.json()["leagues"][HOME_KEY]["corrections"] == {"error": "espn down"}


def test_correction_plays_go_to_the_runner_store_for_its_week_else_the_table(client, monkeypatch):
    added = []
    monkeypatch.setattr(api.app.state, "runner", _lane_runner(week=6, store=SimpleNamespace(add=added.extend)), raising=False)
    _stub_correct(monkeypatch, {"weeks": {}, "changed": [6, 4], "plays": [_corr(6), _corr(4, pid=2)]})
    out = api.correction_pass(ref=home_league())
    assert [p["week"] for p in added] == [6] and "plays" not in out
    assert [r["player_id"] for r in db.plays_rows(4, ref=home_league())] == [2] and db.plays_rows(6, ref=home_league()) == []


def test_a_changed_week_expires_its_pages_and_the_roster_only(client, monkeypatch):
    for k in ("league:5", "league:4", "roster"):
        api.cache_for(home_league()).get_or_build(k, lambda: {"k": 1})
    _stub_correct(monkeypatch, {"weeks": {}, "changed": [5]})
    api.correction_pass(ref=home_league())
    exp = {k: api.cache_for(home_league())._d[k].expires for k in ("league:5", "league:4", "roster")}
    assert exp["league:5"] == 0.0 and exp["roster"] == 0.0 and exp["league:4"] > 0.0


def test_a_past_board_shows_its_stored_feed_for_its_two_teams_newest_first(client, monkeypatch):
    monkeypatch.setattr(api.espn_season, "week_state",
                        lambda week=None, **k: {"week": week or 3, "live": False, "me": {"team_id": 7}, "opp": {"team_id": 3}})
    monkeypatch.setattr(api.app.state, "runner", _lane_runner(week=6, store=_store([])), raising=False)
    db.add_plays([_corr(4, pid=1, team=7), {**_corr(4, pid=2, team=3), "at": "2026-10-14T13:00:00-04:00"},
                  _corr(4, pid=3, team=9)], ref=home_league())
    r = client.get("/week/4").json()["plays"]
    assert [(p["player_id"], p["side"]) for p in r] == [(2, "opp"), (1, "me")]


def test_a_settled_week_is_not_read_again_after_expire_everything(client, monkeypatch):
    from ffl import espn_season
    doc = {"matchups": [], "pro_schedule": {}, "ratings": {}}
    db.put_box_doc(4, doc, espn_season.box_hash(doc), 1.0, settled=True, final=True, ref=home_league())
    monkeypatch.setattr(espn_season, "_fetch_box_doc", lambda lg, w: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setattr(espn_season, "_parse_box_doc", lambda lg, d: ["BOX"])
    api.expire_everything(ref=home_league())
    lg = SimpleNamespace(espn_request=None, current_week=6)
    assert espn_season.box_scores(lg, 4, ref=home_league()) == ["BOX"]


def _views_pool(monkeypatch):
    base = {"bye": False, "injury": "ACTIVE", "opponent": "BUF", "kickoff": None, "return_week": None,
            "injury_note": None, "team": "MIA", "status": "FREEAGENT"}
    rb = [{**base, "player_id": 100 + i, "name": f"R{i}", "pos": "RB", "add": float(i),
           "form": {"snap_pct": 30 + i}} for i in range(100)]
    wr = [{**base, "player_id": 300 + i, "name": f"W{i}", "pos": "WR", "add": float(i) + 0.5, "form": None} for i in range(100)]
    mine = {**base, "player_id": 9, "name": "Mine", "pos": "RB", "add": None, "owner": {"team_id": 7}, "mine": True}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None, **k: {"week": 3, "positions": {"RB": rb, "WR": wr}, "rostered": {"RB": [mine]}})
    api.cache_for(home_league()).clear()


def test_players_applies_the_default_preset_and_url_overrides(client, monkeypatch):
    _views_pool(monkeypatch)
    body = client.get("/players").json()
    assert body["view"] == {"preset": "waivers", "name": "Waivers", "pos": "RB", "fa": True,
                            "cols": ["add", "wk", "ros", "beat", "pace_gap", "opp", "snap_pct"], "filters": [],
                            "sort": {"id": "add", "desc": True}}
    assert body["pos"] == "RB" and len(body["rows"]) == 100 and body["capped"] is False
    assert body["default"] == "waivers" and [p["id"] for p in body["presets"]] == ["waivers"]
    body = client.get("/players?pos=RB&f=snap_pct:ge:120").json()
    assert [r["player_id"] for r in body["rows"]] == [199, 198, 197, 196, 195, 194, 193, 192, 191, 190]
    assert body["view"]["filters"] == [{"field": "snap_pct", "op": "ge", "value": 120.0}]
    assert client.get("/players?pos=RB&fa=0").json()["rows"][-1]["player_id"] == 9, "fa=0 adds the rostered half"


def test_players_avail_theirs_or_mine_reaches_the_rostered_half_whatever_fa_says(client, monkeypatch):
    _views_pool(monkeypatch)
    body = client.get("/players?pos=RB&f=avail:in:mine").json()
    assert body["view"]["fa"] is True
    assert [r["player_id"] for r in body["rows"]] == [9]


def test_players_all_is_capped_unfiltered_and_whole_when_filtered(client, monkeypatch):
    _views_pool(monkeypatch)
    body = client.get("/players?pos=ALL").json()
    assert body["capped"] is True and len(body["rows"]) == 150 and body["pos"] == "ALL"
    body = client.get("/players?pos=ALL&f=snap_pct:le:30").json()
    assert body["capped"] is False and [r["player_id"] for r in body["rows"]] == [100], "a filter reaches the deepest man"
    body = client.get("/players?pos=ALL&q=R1&f=snap_pct:ge:1000").json()
    assert body["rows"], "a search ignores the filters"


def test_players_flex_spans_rb_wr_te_and_is_capped_like_all(client, monkeypatch):
    _views_pool(monkeypatch)
    pool = api.espn_season.free_agent_pool(ref=home_league())
    qb = {**pool["positions"]["RB"][0], "player_id": 500, "name": "Q", "pos": "QB", "add": 999.0}
    monkeypatch.setattr(api.espn_season, "free_agent_pool",
                        lambda week=None, **k: {**pool, "positions": {**pool["positions"], "QB": [qb]}})
    api.cache_for(home_league()).clear()
    body = client.get("/players?pos=FLEX").json()
    assert body["pos"] == "FLEX" and body["capped"] is True and len(body["rows"]) == 150
    assert {r["pos"] for r in body["rows"]} == {"RB", "WR"}, "no QB in FLEX"
    body = client.get("/players?pos=FLEX&f=snap_pct:le:30").json()
    assert body["capped"] is False and [r["player_id"] for r in body["rows"]] == [100]
    assert 500 in {r["player_id"] for r in client.get("/players?pos=ALL").json()["rows"]}


@pytest.mark.parametrize("qs", ["f=snap_pct:gt:5", "f=snap_pct:ge:x", "cols=add,nope", "sort=foo:desc", "pos=XX", "f=bogus"])
def test_players_refuses_a_malformed_view(client, monkeypatch, qs):
    _views_pool(monkeypatch)
    assert client.get(f"/players?{qs}").status_code == 422


def test_player_presets_round_trip(client, monkeypatch):
    _views_pool(monkeypatch)
    new = {"name": "Breakouts", "pos": "ALL", "fa": True, "cols": ["snap_pct", "pace_gap"],
           "filters": [{"field": "snap_pct", "op": "ge", "value": 50}], "sort": {"id": "pace_gap", "desc": True}}
    r = client.post("/players/presets", json=new)
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get("/players/presets").json()["presets"][-1]["name"] == "Breakouts"
    body = client.get(f"/players?preset={pid}").json()
    assert body["view"]["preset"] == pid and body["view"]["pos"] == "ALL" and body["capped"] is False
    assert all((row.get("form") or {}).get("snap_pct", 0) >= 50 for row in body["rows"])
    assert client.post(f"/players/presets/{pid}", json={"name": "Usage"}).json()["presets"][-1]["name"] == "Usage"
    assert client.post("/players/presets/default", json={"id": pid}).json()["default"] == pid
    assert client.get("/players").json()["view"]["preset"] == pid, "the default applies on a bare load"
    assert client.post(f"/players/presets/{pid}/delete").json()["default"] == "waivers"
    assert client.get(f"/players?preset={pid}").json()["view"]["preset"] == "waivers", "a deleted preset's link opens the default"


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "x", "pos": "RB", "fa": True, "cols": ["add", "add"], "filters": [], "sort": {"id": "add", "desc": True}},
                                  {"name": "x", "pos": "RB", "fa": True, "cols": [], "filters": [{"field": "nope", "op": "ge", "value": 1}], "sort": {"id": "add", "desc": True}}])
def test_player_presets_refuse_a_bad_preset_and_keep_the_store(client, body):
    before = client.get("/players/presets").json()
    r = client.post("/players/presets", json=body)
    assert r.status_code == 422 and "error" in r.json()["detail"]
    assert client.get("/players/presets").json() == before


def test_player_presets_unknown_id_is_404(client):
    assert client.post("/players/presets/nope", json={"name": "x"}).status_code == 404
    assert client.post("/players/presets/nope/delete").status_code == 404
    assert client.post("/players/presets/default", json={"id": "nope"}).status_code == 404


def test_news_run_409_when_running(client, monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(api.pipeline, "run", lambda: ran.set() or {"seed": 1})
    flushes = []
    monkeypatch.setattr(api.notify, "flush", lambda: flushes.append(1) or {"disabled": True})
    assert api._NEWS.acquire(blocking=False)
    try:
        assert client.post("/news/run").status_code == 409
    finally:
        api._NEWS.release()
    assert client.post("/news/run").status_code == 202
    api._NEWS_THREAD[0].join(5)
    assert ran.is_set() and flushes == [1] and not api._NEWS.locked()


def test_news_run_skips_the_flush_while_a_tick_holds_it(client, monkeypatch):
    monkeypatch.setattr(api.pipeline, "run", lambda: {})
    flushes = []
    monkeypatch.setattr(api.notify, "flush", lambda: flushes.append(1) or {})
    assert api._NOTIFY.acquire(blocking=False)
    try:
        assert client.post("/news/run").status_code == 202
        api._NEWS_THREAD[0].join(5)
    finally:
        api._NOTIFY.release()
    assert flushes == [] and not api._NEWS.locked()


def test_news_status(client):
    db.put_news_sources([{"id": "pft", "kind": "pft", "name": "Pro Football Talk", "url": "https://x"},
                         {"id": "site:DAL", "kind": "site", "club": "DAL", "name": "dallascowboys.com", "url": "https://y",
                          "active": False}])
    db.put_state("news_last_run", {"at": "2026-10-19T12:00:00-04:00", "stages": {"seed": 2}})
    body = client.get("/news/status").json()
    assert [s["id"] for s in body["sources"]] == ["pft", "site:DAL"]
    assert set(body["sources"][0]) == {"id", "name", "kind", "active", "ok_at", "error", "failing_since"}
    assert body["last_run"]["stages"] == {"seed": 2}
    assert "haiku_usage" not in body and body["jev_usage"] == {}   # the writer's cost is /llm/usage
    db.put_state("news_jev_usage", {"2026-10-19": {"input": 9, "output": 2, "calls": 3}})
    body = client.get("/news/status").json()
    assert body["jev_usage"]["2026-10-19"]["calls"] == 3


def test_llm_usage_is_for_operators_only(client, monkeypatch):
    monkeypatch.delenv("FFL_OPERATOR_EMAILS", raising=False)
    assert client.get("/llm/usage").status_code == 403
    monkeypatch.setenv("FFL_OPERATOR_EMAILS", "someone@else.com")
    r = client.get("/llm/usage")
    assert r.status_code == 403 and r.json()["detail"]["error"] == "not_operator"
    monkeypatch.setenv("FFL_OPERATOR_EMAILS", "other@x.com, TEST@example.com")
    assert client.get("/llm/usage").status_code == 200


def test_llm_usage_totals_by_day_and_kind(client, monkeypatch):
    """GET /llm/usage: llm_calls summed per (UTC day, kind) over the last `days` days; older rows are left out."""
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("FFL_OPERATOR_EMAILS", "test@example.com")
    for kind, cost in (("outlook", 0.02), ("outlook", 0.03), ("news", 0.001)):
        db.add_llm_call(kind=kind, model="sonnet", league="espn:1", player_id=7, input=100, output=10,
                        cost_usd=cost, ok=True)
    old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat(timespec="seconds")
    with db.engine().begin() as c:
        c.execute(db.sa.insert(db.llm_calls).values(at=old, kind="outlook", model="sonnet", input=1, output=1,
                                                    cost_usd=5.0, ok=True))
    body = client.get("/llm/usage?days=7").json()
    today = datetime.now(timezone.utc).date().isoformat()
    assert body["days"] == 7
    assert {r["kind"]: (r["day"], r["calls"], r["input"], round(r["cost_usd"], 4)) for r in body["rows"]} == {
        "outlook": (today, 2, 200, 0.05), "news": (today, 1, 100, 0.001)}
    assert len(client.get("/llm/usage?days=30").json()["rows"]) == 3
    assert client.get("/llm/usage?days=0").status_code == 422


def test_api_feed_orders_by_latest_news(news_client):
    """A backlog run processes old news late: the feed follows the news's time, pages on it, and serves the newest
    source as `latest`."""
    now = datetime.now(timezone.utc)

    def t(h):
        return (now - timedelta(hours=h)).isoformat(timespec="seconds")

    old = _news_story([2], t(26), "old news, processed now")
    db.update_story(old, {"updated_at": t(0)})
    _news_story([2], t(14), "newer")
    grown = _news_story([2], t(48), "first two days ago, updated an hour ago")
    [i] = db.add_news_items([{"source_id": "pft", "ext_id": "u", "url": "https://x/u", "published_at": t(1),
                              "fetched_at": t(1), "title": "u", "text": "", "club": "PHI", "hash": "hu"}])
    db.tag_item(i, [2], None, grown, t(0))
    feed = news_client.get("/news").json()
    assert [s["headline"] for s in feed["stories"]] == ["first two days ago, updated an hour ago", "newer",
                                                       "old news, processed now"]
    assert feed["stories"][0]["latest"]["at"] == t(1) and feed["stories"][0]["at"] == t(1)
    page = news_client.get("/news?limit=1").json()
    assert page["next_before"] == f"{t(1)}|{grown}"
    nxt = news_client.get("/news", params={"limit": 2, "before": page["next_before"]}).json()
    assert [s["headline"] for s in nxt["stories"]] == ["newer", "old news, processed now"]


def _news_story(player_ids, updated_at, headline="H", effect="down"):
    sid = db.add_story({"first_item_id": None, "first_at": updated_at, "updated_at": updated_at, "kind": "injury"})
    db.put_story_players(sid, [{"player_id": p, "effect": effect, "kind": "injury", "prob": 0.9, "confidence": 0.5}
                               for p in player_ids])
    db.put_story_words(sid, 1, headline, "O.", {p: ("R.", False) for p in player_ids})
    return sid


@pytest.fixture
def news_client(client, monkeypatch):
    from ffl.newsdesk.players import Index
    index = Index(by_id={1: {"name": "Jalen Hurts", "pos": "QB", "team": "PHI"},
                         2: {"name": "Tank Bigsby", "pos": "RB", "team": "PHI"}})
    monkeypatch.setattr(api.pipeline, "owners", lambda *, ref: ({1: {"team_id": 7, "name": "Me", "abbrev": "ME"}}, 7))
    monkeypatch.setattr(api.news_players, "fetch_index", lambda: index)
    return client


def test_api_news_routes(news_client):
    now = datetime.now(timezone.utc)

    def t(h):
        return (now - timedelta(hours=h)).isoformat(timespec="seconds")

    _news_story([1], t(3), "mine")
    _news_story([2], t(2), "free")
    _news_story([2], t(1), "free again")
    feed = news_client.get("/news").json()
    assert [s["headline"] for s in feed["stories"]] == ["free again", "free", "mine"] and feed["next_before"] is None
    assert feed["stories"][2]["players"][0]["mine"] is True and "owners_error" not in feed
    assert [s["headline"] for s in news_client.get("/news?filter=mine").json()["stories"]] == ["mine"]
    page = news_client.get("/news?filter=fa&limit=1").json()
    assert [s["headline"] for s in page["stories"]] == ["free again"] and page["next_before"]
    nxt = news_client.get("/news", params={"filter": "fa", "limit": 1, "before": page["next_before"]}).json()
    assert [s["headline"] for s in nxt["stories"]] == ["free"]
    assert news_client.get("/news?filter=bogus").status_code == 422

    p = news_client.get("/news/player/2").json()
    assert p["player"] == {"player_id": 2, "name": "Tank Bigsby", "pos": "RB", "team": "PHI", "owner": None, "mine": False}
    assert p["muted"] is False and len(p["stories"]) == 2 and p["stories"][0]["players"][0]["player_id"] == 2
    assert news_client.get("/news/player/1").json()["player"]["mine"] is True

    assert [r["player_id"] for r in news_client.get("/news/search?q=tank").json()["players"]] == [2]
    assert news_client.get("/news/search").json() == {"players": []}

    assert news_client.post("/news/mute/2", json={"muted": True}).json() == {"muted": True}
    assert db.get_state("news_mutes", user_id=0) == [2]
    assert news_client.get("/news/player/2").json()["muted"] is True
    assert news_client.post("/news/mute/2", json={"muted": False}).json() == {"muted": False}
    assert db.get_state("news_mutes", user_id=0) == []


def test_news_hides_stories_that_move_nobody(news_client):
    now = datetime.now(timezone.utc)

    def t(h):
        return (now - timedelta(hours=h)).isoformat(timespec="seconds")

    # newest first: two all-● stories ahead of each mover, so a page of 2 movers has to scan past them
    _news_story([1], t(1), "quiet 1", effect="none")
    _news_story([2], t(2), "quiet 2", effect="none")
    _news_story([2], t(3), "mover 1", effect="up")
    _news_story([1], t(4), "quiet 3", effect="none")
    _news_story([1, 2], t(5), "mover 2", effect="down")
    _news_story([2], t(6), "mover 3", effect="up")

    page = news_client.get("/news?limit=2").json()
    assert [s["headline"] for s in page["stories"]] == ["mover 1", "mover 2"] and page["next_before"]
    nxt = news_client.get("/news", params={"limit": 2, "before": page["next_before"]}).json()
    assert [s["headline"] for s in nxt["stories"]] == ["mover 3"] and nxt["next_before"] is None
    # no switch brings them back: an old ?quiet=1 is ignored
    assert [s["headline"] for s in news_client.get("/news?quiet=1").json()["stories"]] == ["mover 1", "mover 2", "mover 3"]
    # a player's page keeps only the stories he is a subject of (his row ▲/▼), never one that just names him
    assert [s["headline"] for s in news_client.get("/news/player/1").json()["stories"]] == ["mover 2"]


def test_news_story_route(news_client):
    sid = _news_story([2], datetime.now(timezone.utc).isoformat(timespec="seconds"), "quiet", effect="none")
    s = news_client.get(f"/news/story/{sid}").json()
    assert s["id"] == sid and s["headline"] == "quiet" and s["players"][0]["name"] == "Tank Bigsby"
    assert s["players"][0]["owner"] is None
    assert news_client.get("/news/story/99999").status_code == 404
    unwritten = db.add_story({"first_item_id": None, "first_at": "2026-10-19T00:00:00+00:00",
                              "updated_at": "2026-10-19T00:00:00+00:00", "kind": "injury"})
    assert news_client.get(f"/news/story/{unwritten}").status_code == 404


def test_api_news_player_days_window(news_client):
    now = datetime.now(timezone.utc)
    _news_story([2], (now - timedelta(days=5)).isoformat(timespec="seconds"), "old")
    _news_story([2], (now - timedelta(hours=1)).isoformat(timespec="seconds"), "new")
    assert [s["headline"] for s in news_client.get("/news/player/2?days=1").json()["stories"]] == ["new"]
    assert len(news_client.get("/news/player/2?days=30").json()["stories"]) == 2


def test_api_news_serves_untagged_when_owners_fail(news_client, monkeypatch):
    def down(*, ref):
        raise SystemExit("no LEAGUE_ID in .env")

    monkeypatch.setattr(api.pipeline, "owners", down)
    _news_story([1], datetime.now(timezone.utc).isoformat(timespec="seconds"))
    out = news_client.get("/news").json()
    assert out["owners_error"] == "no LEAGUE_ID in .env"
    assert out["stories"][0]["players"][0]["owner"] is None and out["stories"][0]["players"][0]["mine"] is False


def test_cache_load_serves_rows_of_its_league():
    from ffl import leagues
    other = leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)
    api.Cache(120, persist=True, ref=home_league()).get_or_build("roster", lambda: {"league": "home"})
    api.Cache(120, persist=True, ref=other).get_or_build("roster", lambda: {"league": "other"})
    after = api.Cache(120, persist=True, ref=other)
    after.load()
    assert {k: e.payload for k, e in after._d.items()} == {"roster": {"league": "other"}}
    after.drop("roster")
    assert [p["payload"] for p in db.pages(ref=home_league())] == [{"league": "home"}] and db.pages(ref=other) == []
