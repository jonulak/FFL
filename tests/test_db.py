import multiprocessing as mp

import sqlalchemy as sa
import pytest
from ffl import db


def test_fixture_db_is_migrated_and_temp(tmp_path):
    assert "ffl.db" in db.url() and str(tmp_path) in db.url()
    with db._read() as c:
        names = set(sa.inspect(c).get_table_names())
    assert {"transactions", "draft_picks", "results", "plays", "outlooks", "calibration_snapshots",
            "calibration_rows", "sigma_fits", "claim_plans", "alembic_version"} <= names


def test_pragmas():
    with db._read() as c:
        assert c.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert c.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
        assert c.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_unmigrated_db_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{tmp_path / 'empty.db'}")
    db.reset()
    with pytest.raises(db.SchemaError, match="alembic upgrade head"):
        db.engine()


def test_upsert_insert_then_update_then_nothing():
    t = db.results
    row = {"season": 2026, "week": 1, "opponent": "A", "my_score": 1.0, "opp_score": 2.0, "result": "L"}
    with db._write() as c:
        c.execute(db._upsert(t, [row], ["season", "week"], ["opponent", "my_score", "opp_score", "result"]))
    with db._write() as c:
        c.execute(db._upsert(t, [{**row, "result": "W"}], ["season", "week"], ["result"]))
    with db._write() as c:
        c.execute(db._upsert(t, [{**row, "result": "T"}], ["season", "week"], None))
    with db._read() as c:
        assert c.execute(sa.select(t.c.result)).scalars().all() == ["W"]


def test_write_rolls_back_on_error():
    with pytest.raises(ZeroDivisionError):
        with db._write() as c:
            c.execute(db.results.insert().values(season=2026, week=9, opponent="x", my_score=0, opp_score=0, result="T"))
            1 / 0
    with db._read() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.results)).scalar() == 0


def test_write_begins_immediate():
    seen = []
    sa.event.listen(db.engine(), "before_cursor_execute", lambda conn, cur, stmt, *a: seen.append(stmt))
    with db._write():
        pass
    with db._read() as c:
        c.exec_driver_sql("SELECT 1")
    assert [s for s in seen if s.startswith("BEGIN")][:2] == ["BEGIN IMMEDIATE", "BEGIN"]


TX = {"id": "1788937502756-6-DROPPED-4688380", "date": "2026-09-09T03:05", "type": "DROPPED",
      "team": "henry's Imposing Team", "player": "Cam Ward", "action": "DROPPED", "bid": 0, "reasoning": ""}


def test_transactions_round_trip_and_dedupe():
    assert db.add_transactions([TX]) == 1
    assert db.add_transactions([TX]) == 0
    [row] = db.transactions()
    assert row == {**TX, "player_id": 4688380}


def test_transactions_dst_player_id_negative():
    db.add_transactions([{**TX, "id": "1788937502756-6-DROPPED--16020"}])
    assert db.transactions()[0]["player_id"] == -16020


def _sync_worker(url, rows, q):
    import os
    os.environ["FFL_DB_URL"] = url
    from ffl import db as d
    d.reset()
    q.put(d.add_transactions(rows))


def test_two_processes_same_rows_leave_one_copy():
    rows = [{**TX, "id": f"17889375027{i:02d}-6-DROPPED-{i}"} for i in range(50)]
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_sync_worker, args=(db.url(), rows, q)) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(30)
    assert [p.exitcode for p in procs] == [0, 0]
    # one batch per BEGIN IMMEDIATE transaction: one worker inserts all 50, the other finds them all
    assert sorted([q.get(timeout=5), q.get(timeout=5)]) == [0, 50]
    assert len(db.transactions()) == 50


def test_results_upsert_never_deletes_later_weeks():
    w = lambda wk, r: {"week": wk, "opponent": "X", "my_score": 1.0, "opp_score": 0.0, "result": r}
    db.put_results([w(1, "W"), w(2, "W"), w(3, "L")])
    db.put_results([w(1, "L")])  # a stale League that only knows week 1
    assert [(r["week"], r["result"]) for r in db.results_rows()] == [(1, "L"), (2, "W"), (3, "L")]


PICK = {"overall": 1, "round": 1, "pick": 1, "team_id": 6, "team": "henry's Imposing Team", "player_id": 3918298,
        "player": "Josh Allen", "pos": "QB", "nfl": "BUF", "adp_2qb": 1.9, "board_rank_at_pick": 4.0,
        "proj": 360.8, "ts": "2026-09-07T19:00:10"}


def test_picks_keep_every_column_and_upsert():
    db.put_picks([PICK, {**PICK, "overall": 2, "player_id": 4426348, "player": "Jayden Daniels"}])
    db.put_picks([PICK])
    assert db.picks()[0] == PICK and len(db.picks()) == 2


def test_picks_nullable_numbers():
    db.put_picks([{**PICK, "adp_2qb": None, "board_rank_at_pick": None, "proj": None}])
    assert db.picks()[0]["adp_2qb"] is None


def _play(pid, total, at="2026-10-01T20:29:28", team=3):
    return {"at": at, "week": 4, "player_id": pid, "team_id": team, "delta": 0.6, "total": total, "text": "x"}


def test_plays_round_trip_dedupe_and_order():
    assert db.add_plays([_play(1, 3.2), _play(2, 1.0)]) == 2
    assert db.add_plays([_play(1, 3.2)]) == 0
    assert [r["player_id"] for r in db.plays_rows(4)] == [1, 2]
    assert db.plays_rows(5) == []


def test_outlooks_kinds_are_apart_and_upsert():
    db.put_outlooks(5, "week", {1: {"hash": "a", "bullets": ["x"]}})
    db.put_outlooks(5, "waivers-week", {1: {"bullets": ["fa"]}})
    db.put_outlooks(5, "week", {1: {"hash": "b", "bullets": ["y"]}, 2: {"hash": "c", "bullets": []}})
    assert db.outlooks_for(5, "week") == {1: {"hash": "b", "bullets": ["y"]}, 2: {"hash": "c", "bullets": []}}
    assert db.outlooks_for(5, "waivers-week") == {1: {"bullets": ["fa"]}}
    assert db.outlooks_for(6, "week") == {}


CAL = {"player_id": 1, "name": "A", "pos": "QB", "model": 234.3, "l3": 13.7, "weight": 1.12, "games_left": 14}


def test_snapshot_first_build_of_the_week_stands():
    assert db.put_snapshot(4, 4, [CAL]) is True
    assert db.put_snapshot(4, 4, [{**CAL, "model": 1.0}, {**CAL, "player_id": 2}]) is False
    [snap] = db.snapshots()
    assert snap == {"week": 4, "k": 4, "rows": [CAL]}, "a later build adds nothing, not even new players"


def test_sigma_latest_wins_and_history_kept():
    assert db.latest_sigma() is None
    db.put_sigma({"sigma": {"QB": 7.0}, "n": {"QB": 50}, "weeks": [1, 2]}, fitted_at="2026-09-23T07:00:00")
    db.put_sigma({"sigma": {"QB": 7.7}, "n": {"QB": 73}, "weeks": [1, 2, 3, 4]}, fitted_at="2026-10-07T07:00:00")
    assert db.latest_sigma()["sigma"]["QB"] == 7.7
