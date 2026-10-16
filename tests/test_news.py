"""news.py: the Rotowire, Sleeper depth and ESPN news-stamp parsers, and their last-good caches."""
import pytest, requests
from ffl import news

@pytest.fixture(autouse=True)
def clear_caches():
    news._ROTO.clear(); news._DEPTH.clear(); news._INDEX.clear()

def roto(i, type_, published):
    return {"id": i, "type": type_, "headline": f"h{i} ", "story": f"s{i}", "published": published,
            "playerId": 4362628, "links": {"mobile": {"href": f"http://m.espn.go.com/wireless/story?storyId={i}"}}}

def test_parse_rotowire_keeps_rotowire_only_newest_first_three():
    feed = [roto(1, "Rotowire", "2026-09-28T10:00:00Z"), roto(2, "Story", "2026-09-30T10:00:00Z"),
            roto(3, "Rotowire", "2026-09-30T18:44:37Z"), roto(4, "Rotowire", "2026-09-25T10:00:00Z"),
            roto(5, "Rotowire", "2026-09-29T10:00:00Z")]
    got = news.parse_rotowire({"feed": feed})
    assert [g["id"] for g in got] == ["3", "5", "1"]
    assert got[0] == {"id": "3", "headline": "h3", "story": "s3", "published": "2026-09-30T18:44:37Z",
                      "url": "https://www.espn.com/nfl/player/_/id/4362628"}, \
        "the feed's own link redirects to ESPN's home page; his player page carries the blurb"
    assert news.parse_rotowire({}) == [], "a player nobody has written about is an empty list"
    dupe = {**roto(6, "Rotowire", "2026-09-30T18:00:00Z"), "headline": "h3", "story": "s3"}
    assert [g["id"] for g in news.parse_rotowire({"feed": feed + [dupe]})] == ["3", "5", "1"], \
        "the feed repeats a blurb under a new id; the copy does not take a slot"

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

from datetime import date

def chart_row(name, pos, order, slot=None, injury=None):
    return {"name": name, "pos": pos, "slot": slot or pos, "order": order, "injury": injury}

CHART = {"CIN": [chart_row("Joe Burrow", "QB", 1), chart_row("Joe Flacco", "QB", 2),
                 chart_row("Chase Brown", "RB", 1), chart_row("Samaje Perine", "RB", 2),
                 chart_row("Mike Gesicki", "TE", 1), chart_row("Erick All Jr.", "TE", 2),
                 chart_row("Ja'Marr Chase", "WR", 1, "LWR"), chart_row("Tee Higgins", "WR", 1, "RWR"),
                 chart_row("Andrei Iosivas", "WR", 1, "SWR"), chart_row("Colbie Young", "WR", 6, "LWR", "Out")]}

def c(name, pos, team="CIN", pid=1, **kw):
    return {"player_id": pid, "name": name, "pos": pos, "team": team, "injury": "ACTIVE", **kw}

def names(rows): return [r["name"] for r in rows]

def test_teammates_by_position():
    assert names(news.teammates(c("Ja'Marr Chase", "WR"), CHART)) == ["Joe Burrow", "Tee Higgins", "Andrei Iosivas", "Mike Gesicki"]
    assert names(news.teammates(c("Joe Burrow", "QB"), CHART)) == ["Ja'Marr Chase", "Tee Higgins", "Andrei Iosivas", "Mike Gesicki", "Chase Brown"]
    assert names(news.teammates(c("Chase Brown", "RB"), CHART)) == ["Joe Burrow", "Samaje Perine"]
    assert names(news.teammates(c("Evan McPherson", "K"), CHART)) == ["Joe Burrow"]
    assert news.teammates(c("Bengals D/ST", "D/ST"), CHART) == []
    assert news.teammates(c("Nobody", "WR", team="XXX"), CHART) == [], "no chart for his club: nobody"

def test_own_depth_ranks_within_his_position_and_matches_suffixes():
    assert news.own_depth(c("Colbie Young", "WR"), CHART) == {"slot": "LWR", "order": 6, "rank": 4, "of": 4}
    assert news.own_depth(c("Erick All", "TE"), CHART)["rank"] == 2, "norm_name drops the Jr."
    assert news.own_depth(c("Somebody Else", "WR"), CHART) is None, "unmatched is None, never a guess"

def test_context_joins_teammate_status_and_stamps_through_the_index():
    index = {10: {"name": "Joe Burrow", "pos": "QB", "team": "CIN", "last_news": 1790000000000},
             1: {"name": "Ja'Marr Chase", "pos": "WR", "team": "CIN", "last_news": 1790000001000}}
    injuries = {10: {"status": "Out", "note": "Burrow (toe) won't play Sunday."}}
    ctx = news.context(c("Ja'Marr Chase", "WR", pid=1), CHART, index, injuries, [{"id": "9"}])
    burrow = ctx["teammates"][0]
    assert burrow == {"name": "Joe Burrow", "pos": "QB", "rank": 1, "player_id": 10, "status": "Out",
                      "note": "Burrow (toe) won't play Sunday.", "last_news": 1790000000000}
    higgins = ctx["teammates"][1]
    assert higgins["status"] == "Active" and higgins["last_news"] is None, "not in the index: no stamp, no crash"
    assert ctx["depth"]["rank"] == 1 and ctx["rotowire"] == [{"id": "9"}] and ctx["last_news"] == 1790000001000

def test_context_falls_back_to_sleepers_status():
    ctx = news.context(c("Tee Higgins", "WR"), CHART, {}, {}, [])
    young = [m for m in ctx["teammates"] if m["name"] == "Colbie Young"]
    assert [m["status"] for m in young] == ["Out"], "rank 4 WR is outside the top 3, but hurt at his position"
    ctx = news.context(c("Joe Burrow", "QB"), {"CIN": [chart_row("Ja'Marr Chase", "WR", 1, injury="Questionable")]}, {}, {}, [])
    assert ctx["teammates"][0]["status"] == "Questionable"

def test_context_adds_every_hurt_teammate_at_his_position_wherever_the_chart_has_him():
    rows = [chart_row("Tyler Higbee", "TE", 1), chart_row("Colby Parkinson", "TE", 2),
            chart_row("Davis Allen", "TE", 3), chart_row("Terrance Ferguson", "TE", 5, injury="IR"),
            chart_row("Kyren Williams", "RB", 4, injury="Out"), chart_row("Matthew Stafford", "QB", 1)]
    index = {77: {"name": "Terrance Ferguson", "pos": "TE", "team": "LAR", "last_news": 1791422702521},
             78: {"name": "Davis Allen", "pos": "TE", "team": "LAR", "last_news": None}}
    ctx = news.context(c("Tyler Higbee", "TE", team="LAR"), {"LAR": rows}, index, {}, [])
    assert names(ctx["teammates"]) == ["Matthew Stafford", "Terrance Ferguson"], \
        "the TE1 has no TE in scope, but the hurt TE5 joins; a healthy backup and a hurt RB do not"
    ferg = ctx["teammates"][1]
    assert ferg["status"] == "IR" and ferg["rank"] == 4 and ferg["player_id"] == 77
    ctx = news.context(c("Tyler Higbee", "TE", team="LAR"), {"LAR": rows}, index, {78: {"status": "Doubtful", "note": None}}, [])
    assert names(ctx["teammates"]) == ["Matthew Stafford", "Davis Allen", "Terrance Ferguson"], "ESPN's status counts too"

def test_gather_fetches_rotowire_for_hurt_teammates_only(monkeypatch):
    import ffl.espn_season as es
    rows = [chart_row("Tyler Higbee", "TE", 1), chart_row("Terrance Ferguson", "TE", 5, injury="IR"),
            chart_row("Matthew Stafford", "QB", 1)]
    monkeypatch.setattr(news, "fetch_depth", lambda: {"LAR": rows})
    monkeypatch.setattr(news, "fetch_news_index", lambda week: {
        77: {"name": "Terrance Ferguson", "pos": "TE", "team": "LAR", "last_news": None},
        78: {"name": "Matthew Stafford", "pos": "QB", "team": "LAR", "last_news": None}})
    monkeypatch.setattr(es, "fetch_injuries", lambda: {})
    asked = []
    monkeypatch.setattr(news, "fetch_rotowire", lambda pid: asked.append(pid) or [{"id": f"r{pid}"}])
    got = news.gather([c("Tyler Higbee", "TE", team="LAR", pid=1)], 7)
    mates = {m["name"]: m for m in got[1]["teammates"]}
    assert mates["Terrance Ferguson"]["rotowire"] == [{"id": "r77"}]
    assert "rotowire" not in mates["Matthew Stafford"], "a healthy teammate's blurbs are not fetched"
    assert sorted(asked) == [1, 77]

def test_has_news():
    today = date(2026, 10, 2)
    quiet = {"teammates": [{"status": "Active"}], "rotowire": [{"published": "2026-09-15T10:00:00Z"}]}
    assert not news.has_news(c("X", "WR"), quiet, today)
    assert news.has_news(c("X", "WR", injury="QUESTIONABLE"), quiet, today)
    assert news.has_news(c("X", "WR"), {**quiet, "teammates": [{"status": "Out"}]}, today)
    assert news.has_news(c("X", "WR"), {**quiet, "rotowire": [{"published": "2026-09-25T10:00:00Z"}]}, today)
    assert not news.has_news(c("X", "WR"), {**quiet, "rotowire": [{"published": None}]}, today)

def test_gather_builds_every_card_and_survives_dead_sources(monkeypatch):
    import ffl.espn_season as es
    monkeypatch.setattr(news, "fetch_depth", lambda: {})
    monkeypatch.setattr(news, "fetch_news_index", lambda week: {})
    monkeypatch.setattr(es, "fetch_injuries", lambda: {})
    asked = []
    monkeypatch.setattr(news, "fetch_rotowire", lambda pid: asked.append(pid) or [])
    got = news.gather([c("A", "WR", pid=1), c("Bengals D/ST", "D/ST", pid=-16004)], 5)
    assert set(got) == {1, -16004} and got[1] == {"depth": None, "teammates": [], "rotowire": [], "last_news": None}
    assert asked == [1], "a D/ST has no Rotowire page"
