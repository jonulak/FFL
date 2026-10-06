import copy
import json
from pathlib import Path

import pytest

from ffl import espn_write as W

FX = Path(__file__).parent / "fixtures" / "espn_write"


def fx(name):
    return json.loads((FX / name).read_text())


def test_submit_body_matches_what_espn_sent():
    sent = fx("submit_request.json")
    item_add = next(i for i in sent["items"] if i["type"] == "ADD")
    item_drop = next(i for i in sent["items"] if i["type"] == "DROP")
    body = W.submit_body(item_add["playerId"], item_drop["playerId"], sent["scoringPeriodId"], sent["teamId"], "{SWID}")
    assert body == sent


def test_submit_body_without_drop_has_only_the_add():
    body = W.submit_body(-16020, None, 6, 7, "{SWID}")
    assert [i["type"] for i in body["items"]] == ["ADD"] and body["items"][0]["playerId"] == -16020


def test_cancel_body_matches_what_espn_sent():
    sent = fx("cancel_request.json")
    body = W.cancel_body(sent["relatedTransactionId"], sent["scoringPeriodId"], sent["teamId"], "{SWID}")
    assert body == sent


def test_parse_pending_reads_the_fixture_in_sub_order():
    doc = fx("pending_response.json")
    # the fixture's array order is the reverse of priority
    assert doc["pendingTransactions"][0]["subOrder"] == 2
    claims = W.parse_pending(doc, team_id=fx("submit_request.json")["teamId"])
    assert len(claims) == 2 and all(set(c) == {"id", "add", "drop"} for c in claims)
    assert all(isinstance(c["add"], int) for c in claims)
    assert [c["add"] for c in claims] == [15683, 3049899]
    assert claims[0]["id"] == fx("submit_response.json")["id"]


def test_parse_pending_ignores_other_teams_and_non_waiver():
    doc = fx("pending_response.json")
    first = doc["pendingTransactions"][0]
    other = copy.deepcopy(first)
    other["teamId"] = 3
    fa = copy.deepcopy(first)
    fa["type"] = "FREEAGENT"
    done = copy.deepcopy(first)
    done["status"] = "EXECUTED"
    doc["pendingTransactions"] = [other, fa, done]
    assert W.parse_pending(doc, team_id=7) == []


def test_writes_off_is_a_fake_store(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "0")
    W._FAKE.clear()
    monkeypatch.setattr(W.requests, "post", lambda *a, **k: pytest.fail("network"))
    a = W.submit_claim(101, 1, 6)
    b = W.submit_claim(102, None, 6)
    assert [c["add"] for c in W.pending_claims()] == [101, 102]
    W.cancel_claim(a, 6)
    assert W.pending_claims() == [{"id": b, "add": 102, "drop": None}]


def test_add_body_is_a_freeagent_transaction():
    b = W.add_body(101, 1, 6, 7, "{SWID}")
    assert b["type"] == "FREEAGENT" and [i["type"] for i in b["items"]] == ["ADD", "DROP"]
    # bead ffl-ut0.7 captures the real FREEAGENT request; then replace this with an equality check


def test_add_free_agent_writes_off_records_without_network(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "0")
    W._FAKE_ADDS.clear()
    monkeypatch.setattr(W.requests, "post", lambda *a, **k: pytest.fail("network"))
    W.add_free_agent(101, 1, 6)
    assert W._FAKE_ADDS == [{"add": 101, "drop": 1}]


def test_http_error_raises_write_error(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")

    class R:
        status_code = 403
        text = '{"messages":["not authorized"]}'

    monkeypatch.setattr(W.requests, "post", lambda *a, **k: R())
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    with pytest.raises(W.WriteError) as e:
        W.submit_claim(101, 1, 6)
    assert e.value.expired and "not authorized" in e.value.detail


def test_live_submit_and_cancel_send_what_espn_expects(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    sent = []

    class R:
        status_code = 200

        def json(self):
            return {"id": "abc-123"}

    def post(url, json=None, headers=None, cookies=None, timeout=None):
        sent.append((url, json, headers))
        return R()

    monkeypatch.setattr(W.requests, "post", post)
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    assert W.submit_claim(101, 1, 5) == "abc-123"
    W.cancel_claim("abc-123", 5)
    assert sent[0][1]["executionType"] == "EXECUTE" and sent[1][1]["executionType"] == "CANCEL"
    assert sent[1][1]["relatedTransactionId"] == "abc-123"
    assert all(u == W.WRITE_URL for u, _, _ in sent)
    assert sent[0][2]["x-fantasy-source"] == "kona" and sent[0][2]["x-fantasy-platform"] == "espn-fantasy-web"


def test_pending_claims_wraps_read_errors(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")

    def boom(view):
        raise RuntimeError("nope")

    monkeypatch.setattr(W.config, "raw_league_get", boom)
    with pytest.raises(W.WriteError) as e:
        W.pending_claims()
    assert e.value.status == 401
