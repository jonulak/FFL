"""Where the watcher's pick log goes: the DB for the real draft, the CSV for FFL_DATA_DIR runs."""
from ffl import db
from ffl import draft_watch as dw

from .test_watcher_rollout import _watcher


def _picks(players):
    return [{"teamId": t, "playerId": int(players.iloc[i].player_id), "overallPickNumber": i + 1,
             "roundId": 1, "roundPickNumber": i + 1} for i, t in enumerate([1, 2])]


def test_real_draft_picks_go_to_the_db(monkeypatch, tmp_path):
    w, players = _watcher(monkeypatch, tmp_path, None)
    monkeypatch.setattr(dw, "PICKS_CSV", None)
    w.tick(_picks(players), on_clock=3)
    got = db.picks()
    assert [r["overall"] for r in got] == [1, 2]
    assert set(got[0]) == set(db._PICK_COLS)
    assert got[0]["team"] == "Team 1" and got[0]["player_id"] == int(players.iloc[0].player_id)
    assert got[0]["proj"] is not None and got[0]["ts"]
    assert len((tmp_path / "picks.csv").read_text().splitlines()) == 1  # the header __init__ wrote, no pick rows


def test_mock_run_keeps_its_csv_and_leaves_the_db_alone(monkeypatch, tmp_path):
    w, players = _watcher(monkeypatch, tmp_path, None)
    w.tick(_picks(players), on_clock=3)
    lines = (tmp_path / "picks.csv").read_text().splitlines()
    assert lines[0].startswith("overall,round,pick,team_id,team,player_id,player,pos,nfl,adp_2qb")
    assert len(lines) == 3 and lines[1].startswith("1,1,1,1,Team 1,")
    assert db.picks() == []
