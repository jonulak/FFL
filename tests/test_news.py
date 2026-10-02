"""news.py: the Rotowire, Sleeper depth and ESPN news-stamp parsers, and their last-good caches."""
import pytest, requests
from ffl import news

@pytest.fixture(autouse=True)
def clear_caches():
    news._ROTO.clear(); news._DEPTH.clear(); news._INDEX.clear()

def roto(i, type_, published):
    return {"id": i, "type": type_, "headline": f"h{i} ", "story": f"s{i}", "published": published,
            "links": {"mobile": {"href": f"http://m.espn.go.com/wireless/story?storyId={i}"}}}

def test_parse_rotowire_keeps_rotowire_only_newest_first_three():
    feed = [roto(1, "Rotowire", "2026-09-28T10:00:00Z"), roto(2, "Story", "2026-09-30T10:00:00Z"),
            roto(3, "Rotowire", "2026-09-30T18:44:37Z"), roto(4, "Rotowire", "2026-09-25T10:00:00Z"),
            roto(5, "Rotowire", "2026-09-29T10:00:00Z")]
    got = news.parse_rotowire({"feed": feed})
    assert [g["id"] for g in got] == ["3", "5", "1"]
    assert got[0] == {"id": "3", "headline": "h3", "story": "s3", "published": "2026-09-30T18:44:37Z",
                      "url": "http://m.espn.go.com/wireless/story?storyId=3"}
    assert news.parse_rotowire({}) == [], "a player nobody has written about is an empty list"

def test_fetch_rotowire_serves_last_good_then_empty(monkeypatch):
    class R:
        def raise_for_status(self): pass
        def json(self): return {"feed": [roto(9, "Rotowire", "2026-09-30T10:00:00Z")]}
    monkeypatch.setattr(news.requests, "get", lambda *a, **k: R())
    assert [i["id"] for i in news.fetch_rotowire(7)] == ["9"]
    news._ROTO[7] = (0.0, news._ROTO[7][1])  # expire it
    def boom(*a, **k): raise requests.ConnectionError("down")
    monkeypatch.setattr(news.requests, "get", boom)
    assert [i["id"] for i in news.fetch_rotowire(7)] == ["9"], "outage serves the last good"
    assert news.fetch_rotowire(8) == [], "never fetched + outage = nothing, not an exception"

def test_fetch_rotowire_sends_no_user_agent(monkeypatch):
    seen = {}
    class R:
        def raise_for_status(self): pass
        def json(self): return {"feed": []}
    def get(url, **kw): seen.update(kw); return R()
    monkeypatch.setattr(news.requests, "get", get)
    news.fetch_rotowire(1)
    assert "headers" not in seen and seen["params"] == {"playerId": 1, "limit": news.ROTOWIRE_LIMIT}

def test_parse_depth_maps_clubs_sorts_and_drops_unordered():
    dump = {"1": {"full_name": "Terry McLaurin", "team": "WAS", "position": "WR", "depth_chart_position": "LWR", "depth_chart_order": 1, "injury_status": None},
            "2": {"full_name": "Deebo Samuel", "team": "WAS", "position": "WR", "depth_chart_position": "SWR", "depth_chart_order": 1, "injury_status": "Questionable"},
            "3": {"full_name": "Jayden Daniels", "team": "WAS", "position": "QB", "depth_chart_position": "QB", "depth_chart_order": 1},
            "4": {"full_name": "Practice Guy", "team": "WAS", "position": "WR", "depth_chart_position": None, "depth_chart_order": None},
            "5": {"full_name": "Free Agent", "team": None, "position": "WR", "depth_chart_order": 1},
            "6": {"full_name": "A Linebacker", "team": "WAS", "position": "LB", "depth_chart_order": 1}}
    got = news.parse_depth(dump)
    assert list(got) == ["WSH"], "Sleeper's WAS is espn-api's WSH"
    assert [(r["pos"], r["name"]) for r in got["WSH"]] == [("QB", "Jayden Daniels"), ("WR", "Terry McLaurin"), ("WR", "Deebo Samuel")]
    assert got["WSH"][2] == {"name": "Deebo Samuel", "pos": "WR", "slot": "SWR", "order": 1, "injury": "Questionable"}

def test_parse_news_index_reads_ids_positions_clubs_and_stamps():
    payload = {"players": [{"player": {"id": 3918298, "fullName": "Josh Allen", "defaultPositionId": 1, "proTeamId": 2, "lastNewsDate": 1790802656000}},
                           {"player": {"id": 5, "fullName": "No News", "defaultPositionId": 3, "proTeamId": 28}},
                           {"player": {"id": 6, "fullName": "Defensive Guy", "defaultPositionId": 9, "proTeamId": 2}}]}
    got = news.parse_news_index(payload)
    assert got[3918298] == {"name": "Josh Allen", "pos": "QB", "team": "BUF", "last_news": 1790802656000}
    assert got[5] == {"name": "No News", "pos": "WR", "team": "WSH", "last_news": None}
    assert 6 not in got

def test_fetch_depth_and_index_return_empty_when_never_reached(monkeypatch):
    def boom(*a, **k): raise requests.ConnectionError("down")
    monkeypatch.setattr(news.requests, "get", boom)
    monkeypatch.setattr(news, "raw_league_get", boom)
    assert news.fetch_depth() == {} and news.fetch_news_index(5) == {}
