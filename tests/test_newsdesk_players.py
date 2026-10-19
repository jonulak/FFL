import json
from pathlib import Path

import pytest

from ffl.newsdesk import players

FIXTURE = Path(__file__).parent / "fixtures" / "newsdesk" / "espn_players.json"
KYREN, CORUM, BIJAN, BRIAN, LOVE, JOSH, KYLE, MARIOTA, DANIELS, GRUPE = (
    4430737, 4429096, 4430807, 4241474, 4036378, 3918298, 3115293, 2576980, 4426348, 4259619)


@pytest.fixture(scope="module")
def index():
    return players.build_index(json.loads(FIXTURE.read_text()))


def test_full_name_matches_league_wide(index):
    assert players.match("Blake Corum will carry the load", index) == [CORUM]


def test_surname_only_needs_club(index):
    assert players.match("Rams RB Corum ran well", index) == []
    assert players.match("Rams RB Corum ran well", index, clubs=("LAR",)) == [CORUM]


def test_ambiguous_surname_matches_nobody(index):
    assert players.match("Allen was limited", index, clubs=("BUF",)) == []
    assert players.match("Bills QB Allen was limited", index, clubs=("BUF",)) == []


def test_full_name_disambiguates(index):
    assert players.match("Josh Allen was limited", index, clubs=("BUF",)) == [JOSH]
    assert players.match("Josh Allen was limited", index) == [JOSH]
    assert players.match("Kyle Allen was limited", index) == [KYLE]


def test_common_word_surname_needs_full_name(index):
    assert players.match("Love threw for 300", index, clubs=("GB",)) == []
    assert players.match("Packers QB Love threw for 300", index, clubs=("GB",)) == []
    assert players.match("Jordan Love threw", index, clubs=("GB",)) == [LOVE]


def test_surname_lowercase_not_matched(index):
    assert players.match("the corum package", index, clubs=("LAR",)) == []


def test_garbled_caption_fuzzy_in_club(index):
    assert players.match("Bejon Robinson had a big day", index, clubs=("ATL",)) == [BIJAN]
    assert players.match("Bejon Robinson had a big day", index) == []


def test_ordered_by_first_mention_without_duplicates(index):
    text = "Rams RB Corum spelled Kyren Williams, then Blake Corum scored"
    assert players.match(text, index, clubs=("LAR",)) == [CORUM, KYREN]


def test_build_index_filters(index):
    assert set(index.by_id) == {KYREN, CORUM, BIJAN, BRIAN, LOVE, JOSH, KYLE, MARIOTA, DANIELS, GRUPE}
    assert index.by_id[MARIOTA] == {"name": "Marcus Mariota", "pos": "QB", "team": "WSH"}
    assert set(index.by_club["LAR"]) == {KYREN, CORUM, GRUPE}
    assert index.by_full["blake corum"] == [CORUM]
    names = {v["name"] for v in index.by_id.values()}
    # no club / no ownership (stale record) / a defender
    assert not names & {"Tavon Austin", "Sony Michel", "DJ Davidson"}


def test_fetch_index_serves_last_good_on_failure(monkeypatch):
    players._INDEX.clear()
    monkeypatch.setattr(players, "MIN_INDEX", 1)  # the fixture is a handful of players
    raw = json.loads(FIXTURE.read_text())

    def down():
        raise players.requests.ConnectionError()

    monkeypatch.setattr(players, "_public_players", lambda: raw)
    first = players.fetch_index()
    assert KYREN in first.by_id
    players._INDEX[0] = 0  # expired
    monkeypatch.setattr(players, "_public_players", down)
    monkeypatch.setattr(players, "_league_players", down)
    assert players.fetch_index() is first
    players._INDEX.clear()
    with pytest.raises(players.requests.ConnectionError):
        players.fetch_index()


def test_fetch_index_falls_back_to_league_view(monkeypatch):
    players._INDEX.clear()
    monkeypatch.setattr(players, "MIN_INDEX", 1)
    raw = json.loads(FIXTURE.read_text())

    def down():
        raise players.requests.ConnectionError()

    monkeypatch.setattr(players, "_public_players", down)
    monkeypatch.setattr(players, "_league_players", lambda: raw)
    assert CORUM in players.fetch_index().by_id
    players._INDEX.clear()


def _fake_players(n):
    return [{"id": 10_000 + i, "fullName": f"Player Number{i}", "defaultPositionId": 3, "proTeamId": 14,
             "ownership": {"percentOwned": 1.0}} for i in range(n)]


def test_short_index_is_a_failure(monkeypatch):
    players._INDEX.clear()
    monkeypatch.setattr(players, "_public_players", lambda: _fake_players(50))
    monkeypatch.setattr(players, "_league_players", lambda: _fake_players(50))
    with pytest.raises(ValueError, match="short"):
        players.fetch_index()  # never good: raises rather than install 50
    assert players._INDEX == []
    monkeypatch.setattr(players, "_public_players", lambda: _fake_players(600))
    good = players.fetch_index()
    assert len(good.by_id) == 600 and good.owned[10_000] == 1.0
    players._INDEX[0] = 0  # expired; the public view answers short, the league view too
    monkeypatch.setattr(players, "_public_players", lambda: _fake_players(50))
    assert players.fetch_index() is good
    players._INDEX[0] = 0  # a short public answer falls back to the league view
    monkeypatch.setattr(players, "_public_players", lambda: [])
    monkeypatch.setattr(players, "_league_players", lambda: _fake_players(700))
    assert len(players.fetch_index().by_id) == 700
    players._INDEX.clear()
