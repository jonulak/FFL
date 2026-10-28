"""espn_write per league: the write URL, my team, the pending read and its reuse slot, the FFL_WRITES=0 fakes."""
import pytest
import requests

from ffl import config, espn_season, espn_write as W, leagues
from tests.home import connect_home, home_league


@pytest.fixture
def other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)


def _team(lg, ref):
    return 3 if ref.key == "espn:999" else 7


def test_write_url_per_league(other):
    assert "/leagues/999/" in W.write_url(ref=other)
    assert f"/leagues/{home_league().provider_id}/" in W.write_url(ref=home_league())


def test_bodies_carry_the_league_team(other, monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    sent = []

    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"status": "EXECUTED", "id": 5}

    monkeypatch.setattr(requests, "post", lambda url, json=None, **k: sent.append((url, json)) or R())
    monkeypatch.setattr(espn_season, "my_team_id", _team)
    monkeypatch.setattr(espn_season, "fresh_league", lambda ref=None: None)
    W.move_lineup([(11, "BE", "RB")], 7, ref=other)
    W.submit_claim(11, None, 7, ref=other)
    W.cancel_claim("c1", 7, ref=other)
    W.add_free_agent(11, None, 7, ref=other)
    assert len(sent) == 4
    for url, body in sent:
        assert "/leagues/999/" in url and body["teamId"] == 3
    sent.clear()
    W.reorder_claims(["a"], ref=other)
    (url, _), = sent
    assert "/leagues/999/" in url and "/teams/3/pendingTransactions" in url
    sent.clear()
    W.submit_claim(11, None, 7, ref=home_league())
    assert sent[0][1]["teamId"] == 7 and f"/leagues/{home_league().provider_id}/" in sent[0][0]


def test_fake_store_per_league(other):
    W.move_lineup([(11, "BE", "RB")], 7, ref=other)
    assert W.fake_slots(ref=other) == {11: "RB"} and W.fake_slots(ref=home_league()) == {}
    W.submit_claim(12, None, 7, ref=other)
    assert [c["add"] for c in W.pending_state(ref=other)[0]] == [12]
    assert W.pending_state(ref=home_league())[0] == []


def test_pending_read_reuse_per_league(other, monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    asked = []

    def doc(team, pid):
        return {"scoringPeriodId": 7, "pendingTransactions": [
            {"id": pid, "teamId": team, "type": "WAIVER", "status": "PENDING", "subOrder": 1,
             "items": [{"type": "ADD", "playerId": pid}]}]}

    def get(view, *a, ref=None, **k):
        asked.append(ref)
        return doc(3, 30) if ref.key == "espn:999" else doc(7, 70)

    monkeypatch.setattr(config, "raw_league_get", get)
    monkeypatch.setattr(espn_season, "my_team_id", _team)
    monkeypatch.setattr(espn_season, "fresh_league", lambda ref=None: None)
    assert [c["add"] for c in W.pending_state(60, ref=home_league())[0]] == [70]
    assert [c["add"] for c in W.pending_state(60, ref=other)[0]] == [30]
    assert [c["add"] for c in W.pending_state(60, ref=home_league())[0]] == [70]
    assert [c["add"] for c in W.pending_state(60, ref=other)[0]] == [30]
    assert len(asked) == 2 and asked[1].key == "espn:999"


@pytest.fixture(autouse=True)
def _connected():
    """Real writes go out under the member's own cookies (ffl-soc.3)."""
    connect_home()
