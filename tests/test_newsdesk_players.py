import json
from pathlib import Path

import pytest

from ffl.newsdesk import players

FIXTURE = Path(__file__).parent / "fixtures" / "newsdesk" / "sleeper_players.json"
KYREN, CORUM, BIJAN, DAMARION, MARIOTA = 4361741, 4429019, 4430807, 4000001, 2576980


@pytest.fixture(scope="module")
def index():
    return players.build_index(json.loads(FIXTURE.read_text()))


def test_full_name_matches_league_wide(index):
    assert players.match("Blake Corum will carry the load", index) == [CORUM]


def test_surname_only_needs_club(index):
    assert players.match("Corum ran well", index) == []
    assert players.match("Corum ran well", index, clubs=("LAR",)) == [CORUM]


def test_ambiguous_surname_matches_nobody(index):
    assert players.match("Williams was limited", index, clubs=("LAR",)) == []


def test_full_name_disambiguates(index):
    assert players.match("Kyren Williams was limited", index, clubs=("LAR",)) == [KYREN]
    assert players.match("Kyren Williams was limited", index) == [KYREN]


def test_garbled_caption_fuzzy_in_club(index):
    assert players.match("Bejon Robinson had a big day", index, clubs=("ATL",)) == [BIJAN]
    assert players.match("Bejon Robinson had a big day", index) == []


def test_ordered_by_first_mention_without_duplicates(index):
    text = "Corum spelled Kyren Williams, then Blake Corum scored"
    assert players.match(text, index, clubs=("LAR",)) == [CORUM, KYREN]


def test_build_index_filters(index):
    ids = set(index.by_id)
    assert ids == {KYREN, CORUM, BIJAN, DAMARION, MARIOTA, 4426348}
    assert index.by_id[MARIOTA] == {"name": "Marcus Mariota", "pos": "QB", "team": "WSH"}
    assert set(index.by_club["LAR"]) == {KYREN, CORUM, DAMARION}
    assert index.by_full["blake corum"] == [CORUM]
    names = {v["name"] for v in index.by_id.values()}
    assert not names & {"Brian Robinson", "Sam Hartman", "Athan Kaliakmanis"}  # no espn_id / inactive / OL


def test_fetch_index_serves_last_good_on_failure(monkeypatch):
    players._INDEX.clear()
    raw = json.loads(FIXTURE.read_text())

    class Ok:
        def raise_for_status(self): pass
        def json(self): return raw

    monkeypatch.setattr(players.requests, "get", lambda *a, **k: Ok())
    first = players.fetch_index()
    players._INDEX[0] = 0  # expired
    monkeypatch.setattr(players.requests, "get", lambda *a, **k: (_ for _ in ()).throw(players.requests.ConnectionError()))
    assert players.fetch_index() is first
    players._INDEX.clear()
    with pytest.raises(players.requests.ConnectionError):
        players.fetch_index()
