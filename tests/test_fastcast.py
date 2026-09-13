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
