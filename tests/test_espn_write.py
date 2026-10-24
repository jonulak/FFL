import copy
import json
from datetime import datetime
from pathlib import Path

import pytest
import requests
from espn_api.requests.espn_requests import ESPNAccessDenied, ESPNInvalidLeague, ESPNUnknownError

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
    assert len(claims) == 2 and all(set(c) == {"id", "add", "drop", "priority"} for c in claims)
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
    assert W.pending_claims() == [{"id": b, "add": 102, "drop": None, "priority": 1}]


def test_add_body_matches_the_freeagent_request():
    sent = fx("add_request.json")
    item_add = next(i for i in sent["items"] if i["type"] == "ADD")
    item_drop = next(i for i in sent["items"] if i["type"] == "DROP")
    body = W.add_body(item_add["playerId"], item_drop["playerId"], sent["scoringPeriodId"], sent["teamId"], "{SWID}")
    assert body == sent


def test_add_body_items_agree_with_espns_executed_record():
    rec = fx("add_executed_record.json")
    body = W.add_body(4596334, 4880281, rec["scoringPeriodId"], rec["teamId"], "{SWID}")
    for k in ("type", "executionType", "teamId", "scoringPeriodId", "memberId", "isLeagueManager"):
        assert body[k] == rec[k]
    for sent, done in zip(body["items"], rec["items"], strict=True):
        assert sent.items() <= done.items()


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
    assert all(u == W.write_url() for u, _, _ in sent)
    assert sent[0][2]["x-fantasy-source"] == "kona" and sent[0][2]["x-fantasy-platform"] == "espn-fantasy-web"


def _read_fails(monkeypatch, exc):
    monkeypatch.setenv("FFL_WRITES", "1")

    def boom(view):
        raise exc

    monkeypatch.setattr(W.config, "raw_league_get", boom)
    with pytest.raises(W.WriteError) as e:
        W.pending_claims()
    return e.value


def test_pending_claims_wraps_read_errors(monkeypatch):
    e = _read_fails(monkeypatch, RuntimeError("nope"))
    assert e.status == 0 and not e.expired and e.message == W.UNREACHABLE and "nope" in str(e)


def _http(status):
    r = requests.Response()
    r.status_code = status
    return requests.HTTPError(f"{status} error", response=r)


@pytest.mark.parametrize("exc,status", [
    (ESPNAccessDenied("League 1 cannot be accessed with the provided credentials"), 401),
    (ESPNUnknownError("ESPN returned an HTTP 403"), 403),
    (_http(401), 401),
    (_http(403), 403),
])
def test_only_a_real_401_or_403_reads_as_login_expired(monkeypatch, exc, status):
    e = _read_fails(monkeypatch, exc)
    assert e.status == status and e.expired and e.message == W.EXPIRED


@pytest.mark.parametrize("exc,status", [
    (ESPNUnknownError("ESPN returned an HTTP 503"), 503),
    (ESPNInvalidLeague("League 1 does not exist"), 404),
    (_http(500), 500),
])
def test_other_http_statuses_keep_their_code(monkeypatch, exc, status):
    e = _read_fails(monkeypatch, exc)
    assert e.status == status and not e.expired and e.message == str(e) and str(status) in e.message


@pytest.mark.parametrize("exc", [
    requests.Timeout("read timed out"),
    requests.ConnectionError("connection reset"),
    requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0),
    json.JSONDecodeError("Expecting value", "<html>", 0),
    ESPNUnknownError("something odd"),
    _http(200),  # a response without an error status is no HTTP failure to blame
])
def test_a_read_without_an_http_error_is_couldnt_reach_espn(monkeypatch, exc):
    e = _read_fails(monkeypatch, exc)
    assert e.status == 0 and not e.expired and e.message == W.UNREACHABLE
    assert type(exc).__name__ in e.detail  # the cause is kept for the log


@pytest.mark.parametrize("doc", [
    {"pendingTransactions": [{"teamId": 7, "type": "WAIVER", "status": "PENDING",
                              "items": [{"playerId": 101, "type": "ADD"}]}]},  # a claim with no id
    {"scoringPeriodId": "six", "pendingTransactions": []},
    ["not", "a", "document"],
])
def test_an_unreadable_pending_document_is_a_write_error(monkeypatch, doc):
    monkeypatch.setenv("FFL_WRITES", "1")
    monkeypatch.setattr(W.config, "raw_league_get", lambda view: doc)
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    with pytest.raises(W.WriteError) as e:
        W.pending_state()
    assert e.value.status == 0 and not e.value.expired and e.value.message == W.UNREACHABLE


def test_submit_claim_without_an_id_in_the_answer_is_a_write_error(monkeypatch):
    _live_add(monkeypatch, {"status": "PENDING"})
    with pytest.raises(W.WriteError) as e:
        W.submit_claim(101, 1, 5)
    assert e.value.status == 200 and not e.value.expired and "no id" in e.value.message


def test_parse_pending_skips_a_row_without_an_add():
    doc = {"pendingTransactions": [
        {"id": "bad", "teamId": 7, "type": "WAIVER", "status": "PENDING", "subOrder": 1,
         "items": [{"playerId": 1, "type": "DROP"}]},
        {"id": "good", "teamId": 7, "type": "WAIVER", "status": "PENDING", "subOrder": 2,
         "items": [{"playerId": 101, "type": "ADD"}, {"playerId": 2, "type": "DROP"}]}]}
    assert W.parse_pending(doc, 7) == [{"id": "good", "add": 101, "drop": 2, "priority": 2}]


def test_pending_state_reads_the_scoring_period(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    monkeypatch.setattr(W.config, "raw_league_get", lambda view: {"scoringPeriodId": 5, "pendingTransactions": []})
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    assert W.pending_state() == ([], 5)


def test_pending_state_writes_off_returns_the_fake_period(monkeypatch):
    monkeypatch.setattr(W, "_FAKE_PERIOD", 9)
    assert W.pending_state()[1] == 9


def _live_add(monkeypatch, body):
    monkeypatch.setenv("FFL_WRITES", "1")

    class R:
        status_code = 200

        def json(self):
            return body

    monkeypatch.setattr(W.requests, "post", lambda *a, **k: R())
    monkeypatch.setattr(W, "_team_id", lambda: 7)


def test_add_free_agent_200_but_pending_is_a_write_error(monkeypatch):
    _live_add(monkeypatch, {"id": "x", "status": "PENDING"})
    with pytest.raises(W.WriteError) as e:
        W.add_free_agent(101, 1, 5)
    assert "PENDING" in str(e.value)


def test_add_free_agent_executed_is_landed(monkeypatch):
    _live_add(monkeypatch, {"id": "x", "status": "EXECUTED"})
    W.add_free_agent(101, 1, 5)


def test_reorder_body_matches_what_espn_sent():
    sent = fx("reorder_request.json")
    assert W.reorder_body([r["id"] for r in sent]) == sent


def test_parse_pending_without_sub_order_reads_unnumbered():
    # a bare mPendingTransactions read: subOrder null, newest first (2026-10-06)
    doc = {"pendingTransactions": [
        {"id": "b", "teamId": 7, "type": "WAIVER", "status": "PENDING", "subOrder": None,
         "items": [{"playerId": 102, "type": "ADD"}]},
        {"id": "a", "teamId": 7, "type": "WAIVER", "status": "PENDING", "subOrder": 1,
         "items": [{"playerId": 101, "type": "ADD"}]}]}
    assert [(c["id"], c["priority"]) for c in W.parse_pending(doc, 7)] == [("a", 1), ("b", None)]


def test_pending_read_asks_for_the_team_view(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    seen = {}

    def fake(view, **k):
        seen["view"] = view
        return {"pendingTransactions": []}

    monkeypatch.setattr(W.config, "raw_league_get", fake)
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    W.pending_state()
    assert "mTeam" in seen["view"]  # without it ESPN leaves subOrder null


def test_reorder_posts_to_the_team_pending_url(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    calls = []

    class R:
        status_code = 200

        @staticmethod
        def json():
            return []

    def post(url, json, **k):
        calls.append((url, json))
        return R()

    monkeypatch.setattr(W.requests, "post", post)
    W.reorder_claims(["x", "y"])
    assert calls[0][0].endswith("/teams/7/pendingTransactions")
    assert calls[0][1] == [{"id": "x", "subOrder": 1}, {"id": "y", "subOrder": 2}]


def test_fake_reorder_sets_the_order(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "0")
    W._FAKE.clear()
    a = W.submit_claim(101, 1, 6)
    b = W.submit_claim(102, 1, 6)
    W.reorder_claims([b, a])
    assert [(c["id"], c["priority"]) for c in W.pending_claims()] == [(b, 1), (a, 2)]


def test_lineup_body_matches_the_captured_swap():
    want = json.loads((FX / "lineup_request.json").read_text())
    got = W.lineup_body([(4426515, "WR", "RB/WR/TE"), (4239993, "RB/WR/TE", "WR")], 5, 7, "{SWID}")
    assert got == want


def test_move_lineup_under_fake_writes_moves_the_fake_slots(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "0")
    monkeypatch.setattr(W, "_FAKE_SLOTS", {})
    W.move_lineup([(1, "BE", "RB/WR/TE"), (2, "RB/WR/TE", "BE")], 5)
    assert W.fake_slots() == {1: "RB/WR/TE", 2: "BE"}


def test_move_lineup_raises_unless_espn_says_executed(monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "1")
    monkeypatch.setattr(W, "_team_id", lambda: 7)
    monkeypatch.setattr(W, "_post", lambda body, url=None: {"status": "PENDING"})
    with pytest.raises(W.WriteError):
        W.move_lineup([(1, "BE", "RB")], 5)


PENDING_DOC = {"scoringPeriodId": 7, "status": {"waiverLastExecutionDate": 1792652400000},
               "pendingTransactions": [{"id": "c1", "teamId": 7, "type": "WAIVER", "status": "PENDING", "subOrder": 1,
                                        "items": [{"playerId": 101, "type": "ADD"}]}]}


def _counted_reads(monkeypatch):
    _live_add(monkeypatch, {"id": "c2"})
    monkeypatch.setattr(W, "_READ", None)
    reads = []
    monkeypatch.setattr(W.config, "raw_league_get", lambda view: reads.append(view) or PENDING_DOC)
    return reads


def test_one_pending_read_serves_the_claims_and_the_last_run_inside_max_age(monkeypatch):
    reads = _counted_reads(monkeypatch)
    assert [c["id"] for c in W.pending_state(max_age=60)[0]] == ["c1"]
    assert W.last_waiver_run(max_age=60) == datetime.fromtimestamp(1792652400).isoformat(timespec="seconds")
    assert W.pending_state(max_age=60)[1] == 7
    assert len(reads) == 1


def test_without_max_age_every_call_reads_espn(monkeypatch):
    reads = _counted_reads(monkeypatch)
    W.pending_state(max_age=60)
    W.pending_claims()
    W.last_waiver_run()
    assert len(reads) == 3


def test_a_write_drops_the_reused_read(monkeypatch):
    reads = _counted_reads(monkeypatch)
    W.pending_state(max_age=60)
    W.submit_claim(102, None, 7)
    W.pending_state(max_age=60)
    assert len(reads) == 2


def test_a_read_that_spans_a_write_is_not_reused(monkeypatch):
    reads = _counted_reads(monkeypatch)

    def read_during_a_write(view):
        reads.append(view)
        W.submit_claim(102, None, 7)  # lands while this read is in flight: its answer may predate the claim
        return PENDING_DOC

    monkeypatch.setattr(W.config, "raw_league_get", read_during_a_write)
    W.pending_state(max_age=60)
    monkeypatch.setattr(W.config, "raw_league_get", lambda view: reads.append(view) or PENDING_DOC)
    W.pending_state(max_age=60)
    assert len(reads) == 2


def test_last_waiver_run_is_none_when_the_read_fails(monkeypatch):
    _counted_reads(monkeypatch)
    monkeypatch.setattr(W.config, "raw_league_get", lambda view: (_ for _ in ()).throw(RuntimeError("down")))
    assert W.last_waiver_run(max_age=60) is None
