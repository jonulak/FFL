"""Play-feed events derived from a rescoring, and the per-week store."""
import json
from datetime import datetime

from ffl import plays

WEEK = {"week": 1, "me": {"team_id": 7, "lineup": [{"player_id": 1, "name": "Me QB", "pos": "QB", "team": "SF"}],
                          "bench": [{"player_id": 9, "name": "Benchy", "pos": "RB", "team": "SF"}]},
        "opp": {"team_id": 3, "lineup": [{"player_id": 2, "name": "Their WR", "pos": "WR", "team": "CHI"}]}}
DOC = {"statistics": {"1": {"gameId": "g1", "stats": {}}, "2": {"gameId": "g2", "stats": {}}},
       "events": [{"id": "g1", "period": 1, "clock": "4:12", "lastPlay": {"playId": "p1", "text": " J.Doe pass to X for 12 yards. "}},
                  {"id": "g2", "period": 2, "clock": "0:30", "lastPlay": {}}]}
AT = datetime(2026, 9, 13, 13, 5, 0)


def test_roster_index_is_starters_only_with_side_and_team():
    idx = plays.roster_index(WEEK)
    assert set(idx) == {1, 2}
    assert idx[1] == {"name": "Me QB", "pos": "QB", "team": "SF", "side": "me", "team_id": 7}
    assert idx[2]["side"] == "opp" and idx[2]["team_id"] == 3


def test_events_from_reports_each_moved_starter_with_play_text():
    rows = plays.events_from({1: 10.0}, {1: 11.2, 2: 0.5, 9: 4.0}, DOC, plays.roster_index(WEEK), AT, 1)
    assert [(r["player_id"], r["delta"], r["total"]) for r in rows] == [(1, 1.2, 11.2), (2, 0.5, 0.5)]
    me = rows[0]
    assert me["text"] == "J.Doe pass to X for 12 yards." and me["quarter"] == 1 and me["clock"] == "4:12"
    assert me["side"] == "me" and me["at"] == "2026-09-13T13:05:00" and me["week"] == 1
    assert rows[1]["text"] is None, "a game with no last play: delta alone"


def test_events_from_skips_unmoved_and_unknown_players():
    rows = plays.events_from({1: 10.0, 2: 3.0}, {1: 10.0, 2: 3.0, 5: 9.0}, DOC, plays.roster_index(WEEK), AT, 1)
    assert rows == []


def test_store_appends_and_reloads_newest_first(tmp_path):
    path = tmp_path / "plays-week-1.jsonl"
    st = plays.Store(path)
    st.add([{"at": "a", "delta": 1.0}, {"at": "b", "delta": 2.0}])
    st.add([{"at": "c", "delta": 3.0}])
    assert [r["at"] for r in st.latest(2)] == ["c", "b"]
    again = plays.Store(path)
    assert [r["at"] for r in again.latest()] == ["c", "b", "a"]
    assert len(path.read_text().splitlines()) == 3


def test_store_add_nothing_writes_nothing(tmp_path):
    path = tmp_path / "plays-week-1.jsonl"
    plays.Store(path).add([])
    assert not path.exists()


def test_week_path_is_under_data_live():
    assert plays.week_path(3).name == "plays-week-3.jsonl" and plays.week_path(3).parent.name == "live"
