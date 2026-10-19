from ffl.newsdesk import feed
from ffl.newsdesk.players import Index

ME, OTHER = 7, 3
OWNERS = {1: {"team_id": ME, "name": "Mine", "abbrev": "ME"}, 2: {"team_id": OTHER, "name": "Theirs", "abbrev": "TH"}}
INDEX = Index(by_id={1: {"name": "Jalen Hurts", "pos": "QB", "team": "PHI"},
                     2: {"name": "Tank Bigsby", "pos": "RB", "team": "PHI"},
                     3: {"name": "Free Agent", "pos": "WR", "team": "DAL"},
                     4: {"name": "Jalen Tolbert", "pos": "WR", "team": "DAL"}},
               owned={1: 99.0, 4: 10.0})
SOURCES = {"pft": {"id": "pft", "name": "PFT", "url": "https://pft.example"},
           "rotowire": {"id": "rotowire", "name": "Rotowire", "url": "https://rw.example"}}


def prow(pid, effect="down", prob=0.9):
    return {"player_id": pid, "effect": effect, "kind": "injury", "prob": prob, "confidence": 0.5, "reason": "R", "flag": False}


def item(i, source, at, url=None, withdrawn=None):
    return {"id": i, "source_id": source, "url": url or f"https://x/{i}", "published_at": at, "withdrawn_at": withdrawn}


def story(*rows, sid=1, rev=1, items=None):
    return {"id": sid, "headline": "H", "overview": "O", "status": "live", "status_note": None, "rev": rev,
            "updated_at": "2026-10-19T12:00:00+00:00",
            "items": items or [item(1, "pft", "2026-10-19T10:00:00+00:00")], "players": list(rows)}


def shape(*stories, owners=OWNERS, **kw):
    return feed.shape(list(stories), owners, ME, INDEX, SOURCES, **kw)


def test_shape_owner_tags():
    rows = shape(story(prow(1), prow(2), prow(3)))[0]["players"]
    by = {r["player_id"]: r for r in rows}
    assert by[1]["mine"] is True and by[1]["owner"] == {"team_id": ME, "name": "Mine", "abbrev": "ME"}
    assert by[2]["mine"] is False and by[2]["owner"]["abbrev"] == "TH"
    assert by[3]["mine"] is False and by[3]["owner"] is None
    assert (by[1]["name"], by[1]["pos"], by[1]["team"]) == ("Jalen Hurts", "QB", "PHI")


def test_owner_read_at_request_time():
    s = story(prow(3))
    assert shape(s, owners={})[0]["players"][0]["owner"] is None
    claimed = shape(s, owners={3: {"team_id": ME, "name": "Mine", "abbrev": "ME"}})[0]["players"][0]
    assert claimed["owner"]["team_id"] == ME and claimed["mine"] is True


def test_keep_filters():
    mixed = shape(story(prow(1), prow(2), prow(3)))[0]
    only_other = shape(story(prow(2)))[0]
    only_fa = shape(story(prow(3)))[0]
    assert all(feed.keep(mixed, f) for f in feed.FILTERS)
    assert not feed.keep(only_other, "mine") and feed.keep(only_other, "others") and not feed.keep(only_other, "fa")
    assert not feed.keep(only_fa, "mine") and feed.keep(only_fa, "fa") and not feed.keep(only_fa, "others")
    assert feed.keep(only_fa, "all")


def test_player_view_only_his_row():
    out = shape(story(prow(1), prow(2)), player=2)[0]
    assert [r["player_id"] for r in out["players"]] == [2]


def test_rows_order():
    rows = shape(story(prow(1, "none", 0.99), prow(2, "up", 0.6), prow(3, "down", 0.9)))[0]["players"]
    assert [r["player_id"] for r in rows] == [3, 2, 1]


def test_story_shape_first_and_sources():
    s = story(prow(1), rev=2, items=[item(2, "rotowire", "2026-10-19T11:00:00+00:00"),
                                     item(1, "pft", "2026-10-19T10:00:00+00:00", url="https://pft.example/a"),
                                     item(3, "pft", "2026-10-19T11:30:00+00:00", withdrawn="2026-10-19T12:00:00+00:00")])
    out = shape(s)[0]
    assert out["first"] == {"source": "PFT", "url": "https://pft.example/a", "at": "2026-10-19T10:00:00+00:00"}
    assert [c["source"] for c in out["sources"]] == ["Rotowire"]
    assert out["updated"] is True and out["at"] == "2026-10-19T12:00:00+00:00"
    assert shape(story(prow(1)))[0]["updated"] is False


def test_search_prefix_first_then_owned():
    assert [p["player_id"] for p in feed.search(INDEX, "jal")] == [1, 4]
    assert [p["player_id"] for p in feed.search(INDEX, "bigs")] == [2]
    assert [p["player_id"] for p in feed.search(INDEX, "ank")] == [2]   # contained, not a prefix
    assert feed.search(INDEX, "  ") == []
    assert feed.search(INDEX, "zzz") == []
