"""ESPN FastCast frames, JSON-Patch, and local scoring — no network."""
import base64
import copy
import json
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ffl import fastcast as fc

FIX = json.loads((Path(__file__).parent / "fixtures" / "fastcast_checkpoint.json").read_text())
# ESPN's own box score points for the fixture's played players (week 1 Thursday, 2026)
ESPN_ACTUAL = {4361741: 21.1, 4430737: 15.5, 4426515: 12.4, 3912547: 0.52, 3126486: 18.0}
SETTINGS = json.loads((Path(__file__).parent / "fixtures" / "settings_scoring.json").read_text())


def r_frame(ops: list, mid: int = 5, op: str = "R") -> str:
    inner = {"ts": 1789317516655, "~c": 1, "pl": base64.b64encode(zlib.compress(json.dumps(ops).encode())).decode()}
    return json.dumps({"tc": fc.TOPIC, "mid": mid, "pl": json.dumps(inner), "op": op})


def test_decode_r_frame_yields_the_patch():
    ops = [{"op": "replace", "path": "/statistics/1/stats/42", "value": 3.0}]
    f = fc.decode(r_frame(ops, mid=9))
    assert f["op"] == "R" and f["mid"] == 9 and f["patch"] == ops


def test_live_push_frames_carry_patches_too():
    """In-game deltas arrive as op P; R is only the replay right after subscribing."""
    ops = [{"op": "replace", "path": "/statistics/1/stats/42", "value": 3.0}]
    assert fc.decode(r_frame(ops, op="P"))["patch"] == ops
    assert "patch" not in fc.decode('{"op":"I","mid":4,"pl":"0"}')


def test_decode_uncompressed_payload_is_tolerated():
    """~c 0 (or absent) means the inner payload is plain JSON."""
    inner = {"ts": 1, "pl": json.dumps([{"op": "add", "path": "/x", "value": 1}])}
    f = fc.decode(json.dumps({"mid": 1, "pl": json.dumps(inner), "op": "R"}))
    assert f["patch"] == [{"op": "add", "path": "/x", "value": 1}]


def test_game_states_by_team_from_the_checkpoint():
    """Each competitor's abbreviation maps to its game's state, period and display clock."""
    doc = {"events": [
        {"id": "a", "status": "in", "fullStatus": {"displayClock": "9:09", "period": 2, "type": {"state": "in"}},
         "competitors": [{"abbreviation": "JAX", "homeAway": "home", "score": 14.0},
                         {"abbreviation": "CLE", "homeAway": "away", "score": 3.0}]},
        {"id": "b", "status": "post", "fullStatus": {"displayClock": "0:00", "period": 4, "type": {"state": "post"}},
         "competitors": [{"abbreviation": "SEA", "homeAway": "home", "score": 20}, {"abbreviation": "NE", "homeAway": "away", "score": 27}]},
        {"id": "c", "status": "pre", "fullStatus": {"displayClock": "0:00", "period": 0, "type": {"state": "pre"}},
         "competitors": [{"abbreviation": "KC", "score": ""}, {"abbreviation": "DEN", "score": ""}]},  # pre: blank
        {"id": "d", "status": "in", "competitors": [{"abbreviation": "X"}]},  # no fullStatus: status alone
    ]}
    g = fc.game_states(doc)
    assert g["JAX"] == g["CLE"] == {"status": "in", "quarter": 2, "clock": "9:09", "score": {"JAX": 14, "CLE": 3}}
    assert g["SEA"] == {"status": "post", "quarter": 4, "clock": "0:00", "score": {"SEA": 20, "NE": 27}}
    assert g["KC"]["status"] == "pre" and g["KC"]["score"] == {}
    assert g["X"] == {"status": "in", "quarter": None, "clock": None, "score": {}}


def test_client_reports_each_failure_as_it_happens():
    """A callback that raises makes run() reconnect; the error must reach on_error at once,
    not only once the loop ends — a crash-reconnect loop otherwise looks like 'connected'."""
    import threading
    seen = []
    c = fc.Client(lambda d: None, on_error=seen.append)
    stop = threading.Event()
    calls = {"n": 0}

    def boom(stop_):
        calls["n"] += 1
        if calls["n"] == 2:
            stop.set()
        raise RuntimeError("bad doc")
    c.run_once = boom
    c.run(stop)
    assert seen == ["RuntimeError: bad doc", "RuntimeError: bad doc"] and c.last_error == "RuntimeError: bad doc"


def test_a_patch_frame_without_a_payload_names_what_it_carried():
    """Seen live 2026-09-28 after Monday night: a patch frame whose inner JSON had no `pl`. The
    reconnect recovers; the error has to say what the frame was, or the next one is a guess too."""
    frame = json.dumps({"op": "P", "mid": 7, "pl": json.dumps({"ts": 1, "~c": 0, "other": "x"})})
    with pytest.raises(ValueError) as e:
        fc.decode(frame)
    assert "['ts', '~c', 'other']" in str(e.value) and '"mid": 7' in str(e.value)


def test_a_fresh_checkpoint_clears_the_last_error(monkeypatch):
    """/live/status reads last_error once the socket drops: a failure the reconnect already
    repaired must not read as the feed's state for the rest of the week."""
    c = fc.Client(lambda d: None)
    c.last_error = "ValueError: old"
    monkeypatch.setattr(fc.requests, "get", lambda *a, **k: type("R", (), {
        "raise_for_status": lambda self: None, "json": lambda self: {"events": []}})())
    c._load_checkpoint(3)
    assert c.last_error is None and c.mid == 3


def test_player_stats_are_the_raw_rows_by_player_id():
    doc = {"statistics": {"7": {"gameId": "a", "stats": {"3": 138.0, "4": 2.0}}, "-16004": {"stats": {"99": 2.0}}}}
    assert fc.player_stats(doc) == {7: {"3": 138.0, "4": 2.0}, -16004: {"99": 2.0}}


def test_decode_uncompressed_payload_may_already_be_the_op_list():
    """Seen live 2026-09-13 (mid 2145730): ~c 0 with `pl` inlined as the JSON list, not a string."""
    ops = [{"op": "replace", "path": "/statistics/4426388/stats/59", "value": 1}]
    inner = {"ts": 1789322635135, "~c": 0, "pl": ops}
    f = fc.decode(json.dumps({"op": "P", "pl": json.dumps(inner), "mid": 2145730, "tc": "t"}))
    assert f["patch"] == ops


def test_heartbeat_and_handshake_frames_decode_without_a_patch():
    assert fc.decode('{"op":"B"}') == {"op": "B"}
    h = fc.decode('{"op":"H","mid":3,"pl":"https://x/checkpoint","tc":"t"}')
    assert h["op"] == "H" and h["pl"] == "https://x/checkpoint"


def test_apply_patch_replace_add_remove_and_new_player():
    doc = {"statistics": {"1": {"stats": {"42": 1.0}}}, "events": [{"id": "a", "status": "pre", "scoringPlays": []}]}
    fc.apply_patch(doc, [
        {"op": "replace", "path": "/statistics/1/stats/42", "value": 9.0},
        {"op": "add", "path": "/statistics/1/stats/43", "value": 1.0},
        {"op": "add", "path": "/statistics/2", "value": {"stats": {"53": 2.0}}},
        {"op": "replace", "path": "/events/0/status", "value": "in"},
        {"op": "add", "path": "/events/0/scoringPlays/-", "value": {"id": "p1"}},
        {"op": "add", "path": "/events/0/scoringPlays/0", "value": {"id": "p0"}},
        {"op": "remove", "path": "/statistics/1/stats/42"},
    ])
    assert doc["statistics"]["1"]["stats"] == {"43": 1.0}
    assert doc["statistics"]["2"]["stats"] == {"53": 2.0}
    assert doc["events"][0]["status"] == "in"
    assert [p["id"] for p in doc["events"][0]["scoringPlays"]] == ["p0", "p1"]


def test_apply_patch_copy_and_move():
    """The feed copies a stat line when one row mirrors another (D/ST and its team)."""
    doc = {"statistics": {"1": {"stats": {"42": 1.0}}}, "events": [{"drive": {"id": "d1"}}]}
    fc.apply_patch(doc, [
        {"op": "copy", "from": "/statistics/1/stats", "path": "/statistics/2"},
        {"op": "move", "from": "/events/0/drive", "path": "/events/0/last"},
    ])
    assert doc["statistics"]["2"] == {"42": 1.0}
    doc["statistics"]["2"]["42"] = 5.0
    assert doc["statistics"]["1"]["stats"]["42"] == 1.0, "a copy, not a shared reference"
    assert doc["events"][0] == {"last": {"id": "d1"}}


def test_apply_patch_escaped_path_and_unknown_op():
    doc = {"a/b": {"~x": 1}}
    fc.apply_patch(doc, [{"op": "replace", "path": "/a~1b/~0x", "value": 2}])
    assert doc == {"a/b": {"~x": 2}}
    with pytest.raises(ValueError):
        fc.apply_patch(doc, [{"op": "test", "path": "/a~1b", "value": 1}])


def test_apply_patch_leaves_the_doc_whole_when_an_op_fails():
    """A bad path mid-patch must not leave half a patch applied: the caller resyncs from the
    checkpoint instead of trusting a torn document."""
    doc = {"statistics": {"1": {"stats": {"42": 1.0}}}}
    before = copy.deepcopy(doc)
    with pytest.raises(KeyError):
        fc.apply_patch(doc, [{"op": "replace", "path": "/statistics/1/stats/42", "value": 5.0},
                             {"op": "replace", "path": "/statistics/9/stats/42", "value": 5.0}])
    assert doc == before


def test_local_scoring_matches_espn_box_score():
    table = fc.scoring_table(SETTINGS)
    pts = fc.score_all(FIX, table)
    for pid, want in ESPN_ACTUAL.items():
        assert pts[pid] == pytest.approx(want, abs=0.005), pid
    assert -14026 in pts, "D/ST rows score too"


def test_scoring_ignores_stat_ids_the_league_does_not_pay_for():
    assert fc.player_points({"22": 205.0, "42": 30.0}, {42: 0.1}) == pytest.approx(3.0)


def test_games_in_progress_reads_event_state():
    doc = copy.deepcopy(FIX)
    assert not fc.games_in_progress(doc)  # fixture: all pre or post
    doc["events"][0]["status"] = "in"
    assert fc.games_in_progress(doc)


def test_next_kickoff_is_the_earliest_future_pre_game():
    now = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    assert fc.next_kickoff(FIX, now) == datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)
    late = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    assert fc.next_kickoff(FIX, late) is None
