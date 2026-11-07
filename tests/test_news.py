"""news.py: the Sleeper depth and ESPN player-index parsers and their last-good caches, and the news desk's
stories as the outlook's sheet reads them."""
import pytest, requests
from ffl import news

@pytest.fixture(autouse=True)
def clear_caches():
    news._DEPTH.clear(); news._INDEX.clear()

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

def test_context_joins_teammate_status_through_the_index():
    index = {10: {"name": "Joe Burrow", "pos": "QB", "team": "CIN", "last_news": 1790000000000}}
    injuries = {10: {"status": "Out", "note": "Burrow (toe) won't play Sunday."}}
    ctx = news.context(c("Ja'Marr Chase", "WR", pid=1), CHART, index, injuries)
    assert ctx["teammates"][0] == {"name": "Joe Burrow", "pos": "QB", "rank": 1, "player_id": 10, "status": "Out",
                                   "note": "Burrow (toe) won't play Sunday."}
    higgins = ctx["teammates"][1]
    assert higgins["status"] == "Active" and higgins["player_id"] is None, "not in the index: no id, no crash"
    assert ctx["depth"]["rank"] == 1 and set(ctx) == {"depth", "teammates"}

def test_context_falls_back_to_sleepers_status():
    ctx = news.context(c("Tee Higgins", "WR"), CHART, {}, {})
    young = [m for m in ctx["teammates"] if m["name"] == "Colbie Young"]
    assert [m["status"] for m in young] == ["Out"], "rank 4 WR is outside the top 3, but hurt at his position"
    ctx = news.context(c("Joe Burrow", "QB"), {"CIN": [chart_row("Ja'Marr Chase", "WR", 1, injury="Questionable")]}, {}, {})
    assert ctx["teammates"][0]["status"] == "Questionable"

def test_context_adds_every_hurt_teammate_at_his_position_wherever_the_chart_has_him():
    rows = [chart_row("Tyler Higbee", "TE", 1), chart_row("Colby Parkinson", "TE", 2),
            chart_row("Davis Allen", "TE", 3), chart_row("Terrance Ferguson", "TE", 5, injury="IR"),
            chart_row("Kyren Williams", "RB", 4, injury="Out"), chart_row("Matthew Stafford", "QB", 1)]
    index = {77: {"name": "Terrance Ferguson", "pos": "TE", "team": "LAR", "last_news": 1791422702521},
             78: {"name": "Davis Allen", "pos": "TE", "team": "LAR", "last_news": None}}
    ctx = news.context(c("Tyler Higbee", "TE", team="LAR"), {"LAR": rows}, index, {})
    assert names(ctx["teammates"]) == ["Matthew Stafford", "Terrance Ferguson"], \
        "the TE1 has no TE in scope, but the hurt TE5 joins; a healthy backup and a hurt RB do not"
    ferg = ctx["teammates"][1]
    assert ferg["status"] == "IR" and ferg["rank"] == 4 and ferg["player_id"] == 77
    ctx = news.context(c("Tyler Higbee", "TE", team="LAR"), {"LAR": rows}, index, {78: {"status": "Doubtful", "note": None}})
    assert names(ctx["teammates"]) == ["Matthew Stafford", "Davis Allen", "Terrance Ferguson"], "ESPN's status counts too"

def test_gather_reads_the_desk_for_him_and_his_hurt_teammates_only(monkeypatch):
    import ffl.espn_season as es
    rows = [chart_row("Tyler Higbee", "TE", 1), chart_row("Terrance Ferguson", "TE", 5, injury="IR"),
            chart_row("Matthew Stafford", "QB", 1)]
    monkeypatch.setattr(news, "fetch_depth", lambda: {"LAR": rows})
    monkeypatch.setattr(news, "fetch_news_index", lambda week: {
        77: {"name": "Terrance Ferguson", "pos": "TE", "team": "LAR", "last_news": None},
        78: {"name": "Matthew Stafford", "pos": "QB", "team": "LAR", "last_news": None}})
    monkeypatch.setattr(es, "fetch_injuries", lambda: {})
    desk = {}
    monkeypatch.setattr(news, "fetch_desk", lambda names, today: desk.update(names=names) or
                        {1: {"stories": [{"id": 9}], "lines": [{"id": 10}]},
                         77: {"stories": [{"id": i} for i in range(1, 6)], "lines": [{"id": 6}]}})
    got = news.gather([c("Tyler Higbee", "TE", team="LAR", pid=1)], 7)
    mates = {m["name"]: m for m in got[1]["teammates"]}
    assert desk["names"] == {1: "Tyler Higbee", 77: "Terrance Ferguson"}
    assert got[1]["stories"] == [{"id": 9}] and got[1]["lines"] == [{"id": 10}]
    assert "lines" not in mates["Terrance Ferguson"]
    assert mates["Terrance Ferguson"]["stories"] == [{"id": 1}, {"id": 2}, {"id": 3}], "a hurt teammate's newest three"
    assert "stories" not in mates["Matthew Stafford"], "a healthy teammate's news is not read"

def test_gather_keeps_a_hurt_teammates_written_stories_past_the_spoken_ones(monkeypatch):
    import ffl.espn_season as es
    rows = [chart_row("Tyler Higbee", "TE", 1), chart_row("Terrance Ferguson", "TE", 5, injury="IR")]
    monkeypatch.setattr(news, "fetch_depth", lambda: {"LAR": rows})
    monkeypatch.setattr(news, "fetch_news_index", lambda week: {
        77: {"name": "Terrance Ferguson", "pos": "TE", "team": "LAR", "last_news": None}})
    monkeypatch.setattr(es, "fetch_injuries", lambda: {})
    newest_first = [{"id": i, "spoken": True} for i in (9, 8, 7, 6)] + [{"id": i, "spoken": False} for i in (5, 4, 3, 2)]
    monkeypatch.setattr(news, "fetch_desk", lambda names, today: {77: {"stories": newest_first, "lines": []}})
    mates = news.gather([c("Tyler Higbee", "TE", team="LAR", pid=1)], 7)[1]["teammates"]
    assert [s["id"] for s in mates[0]["stories"]] == [5, 4, 3], "four new podcast chunks do not push out his report"

def test_has_news():
    today = date(2026, 10, 2)
    quiet = {"teammates": [{"status": "Active"}], "stories": [{"published": "2026-09-15T10:00:00+00:00"}]}
    assert not news.has_news(c("X", "WR"), quiet, today)
    assert news.has_news(c("X", "WR", injury="QUESTIONABLE"), quiet, today)
    assert news.has_news(c("X", "WR"), {**quiet, "teammates": [{"status": "Out"}]}, today)
    assert not news.has_news(c("X", "WR"), {**quiet, "stories": [{"published": None}]}, today)
    assert not news.has_news(c("X", "WR"), {"teammates": []}, today)
    assert news.has_news(c("X", "WR"), {**quiet, "stories": [{"published": "2026-09-30T10:00:00+00:00"}]}, today)
    assert not news.has_news(c("X", "WR"), {**quiet, "stories": [{"published": "2026-09-01T10:00:00+00:00"}]}, today)
    assert news.has_news(c("X", "WR"), {**quiet, "lines": [{"published": "2026-09-30T10:00:00+00:00"}]}, today), \
        "a player with only a passing mention still has news"

def desk_item(i, at, withdrawn=None, text="body"):
    return {"id": i, "source_id": "bsky:x", "url": f"https://x/{i}", "published_at": at, "title": f"title {i}",
            "text": text, "withdrawn_at": withdrawn}

def desk_story(sid, items, headline="", overview="", players=({"player_id": 1, "effect": "down", "reason": "line"},)):
    return {"id": sid, "rev": 1, "status": "live", "latest_at": items[-1]["published_at"] if items else "",
            "headline": headline, "overview": overview, "players": list(players), "items": items}

def test_shape_stories_cites_live_sources_and_quotes_an_unwritten_story():
    sources = {"bsky:x": {"name": "Beat Writer"}}
    written = desk_story(1, [desk_item(1, "2026-10-18T10:00:00"), {**desk_item(5, "2026-10-18T11:00:00"), "url": "https://x/1"},
                             desk_item(2, "2026-10-19T10:00:00", "gone")], headline="H", overview="O")
    raw = desk_story(2, [desk_item(3, "2026-10-17T10:00:00", text="a  long\n" + "x" * 900)])
    dead = desk_story(3, [desk_item(4, "2026-10-17T10:00:00", "gone")])
    w, r = news.shape_stories([written, raw, dead], 1, sources)
    assert (w["headline"], w["overview"], w["text"], w["effect"], w["line"]) == ("H", "O", "", "down", "line")
    assert w["sources"] == [{"site": "Beat Writer", "published": "2026-10-18T11:00:00", "url": "https://x/1"}], \
        "a withdrawn source is gone and one link is cited once, at its newest"
    assert r["headline"] == "title 3" and r["text"].startswith("a long x") and len(r["text"]) == news.DESK_TEXT

def test_shape_stories_marks_a_story_with_only_spoken_live_sources():
    pod, yt = {**desk_item(1, "2026-10-18T10:00:00"), "source_id": "pod:DET"}, {**desk_item(2, "2026-10-18T11:00:00"), "source_id": "yt:DET"}
    text = desk_item(3, "2026-10-18T12:00:00")
    spoken = [s["spoken"] for s in news.shape_stories([
        desk_story(1, [pod, yt]),
        desk_story(2, [pod, text]),
        desk_story(3, [pod, {**text, "withdrawn_at": "gone"}]),
        desk_story(4, [text])], 1, {})]
    assert spoken == [True, False, True, False], "a withdrawn written source does not count"

def test_fetch_desk_reads_the_window_and_degrades_to_empty(monkeypatch):
    asked = {}
    monkeypatch.setattr(news.db, "player_stories", lambda pids, since: asked.update(since=since) or
                        {1: [desk_story(1, [desk_item(1, "2026-10-18T10:00:00")])]})
    monkeypatch.setattr(news.db, "news_sources", lambda active_only=True: [{"id": "bsky:x", "name": "Beat Writer"}])
    got = news.fetch_desk({1: "Lamar Jackson"}, date(2026, 10, 20))
    assert asked["since"] == "2026-10-10" and got[1]["stories"][0]["sources"][0]["site"] == "Beat Writer"
    def boom(pids, since):
        raise news.db.DBError("locked")
    monkeypatch.setattr(news.db, "player_stories", boom)
    assert news.fetch_desk({1: "Lamar Jackson"}, date(2026, 10, 20)) == {}

def test_fetch_desk_splits_stories_from_lines(monkeypatch):
    him = {"player_id": 1, "effect": "none", "reason": None}
    mine = desk_story(1, [{**desk_item(1, "2026-10-18T10:00:00"), "ext_id": "1"}], headline="Lamar Jackson hurt", players=(him,))
    other = desk_story(2, [{**desk_item(2, "2026-10-19T10:00:00", text="Ravens said that Lamar Jackson has a thumb injury."),
                            "ext_id": "2"}], headline="Ravens sign a QB", players=(him,))
    monkeypatch.setattr(news.db, "player_stories", lambda pids, since: {1: [other, mine]})
    monkeypatch.setattr(news.db, "news_sources", lambda active_only=True: [{"id": "bsky:x", "name": "Beat Writer"}])
    got = news.fetch_desk({1: "Lamar Jackson"}, date(2026, 10, 20))[1]
    assert [s["id"] for s in got["stories"]] == [1]
    assert got["lines"] == [{"id": 2, "text": "Ravens said that Lamar Jackson has a thumb injury.", "published": "2026-10-19T10:00:00",
                             "sources": [{"site": "Beat Writer", "published": "2026-10-19T10:00:00", "url": "https://x/2"}]}]

def test_gather_builds_every_card_and_survives_dead_sources(monkeypatch):
    import ffl.espn_season as es
    monkeypatch.setattr(news, "fetch_depth", lambda: {})
    monkeypatch.setattr(news, "fetch_news_index", lambda week: {})
    monkeypatch.setattr(es, "fetch_injuries", lambda: {})
    monkeypatch.setattr(news, "fetch_desk", lambda names, today: {})
    got = news.gather([c("A", "WR", pid=1), c("Bengals D/ST", "D/ST", pid=-16004)], 5)
    assert set(got) == {1, -16004} and got[1] == {"depth": None, "teammates": [], "stories": [], "lines": []}
