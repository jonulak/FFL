import multiprocessing as mp
import sqlite3

import sqlalchemy as sa
import pytest
from ffl import db


def test_fixture_db_is_migrated_and_temp(tmp_path):
    assert "ffl.db" in db.url() and str(tmp_path) in db.url()
    with db._read() as c:
        names = set(sa.inspect(c).get_table_names())
    assert {"transactions", "draft_picks", "results", "plays", "outlooks", "calibration_snapshots",
            "calibration_rows", "sigma_fits", "claim_plans", "alembic_version", "box_scores", "gap_games",
            "box_score_corrections"} <= names


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


def test_existing_db_without_the_head_schema_raises(monkeypatch, tmp_path):
    path = tmp_path / "bare.db"
    sqlite3.connect(path).close()  # the file exists, so the missing-file branch cannot be the one that fires
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{path}")
    db.reset()
    with pytest.raises(db.SchemaError, match="is at schema None"):
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
    assert db.add_transactions([TX]) == [{**TX, "player_id": 4688380}]
    assert db.add_transactions([TX]) == []
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
    q.put(len(d.add_transactions(rows)))


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


def test_claim_plan_round_trip_and_replace():
    assert db.claim_plan(6) is None
    db.put_claim_plan({"week": 6, "groups": [{"targets": [101], "cuts": [1], "open": 0}], "submits": []})
    db.put_claim_plan({"week": 6, "groups": [], "submits": []})
    assert db.claim_plan(6) == {"week": 6, "groups": [], "submits": []}


def test_engine_on_a_missing_sqlite_file_raises_and_creates_nothing(tmp_path, monkeypatch):
    missing = tmp_path / "nope.db"
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{missing}")
    db.reset()
    with pytest.raises(db.SchemaError, match="alembic upgrade head"):
        db.engine()
    assert not missing.exists()


def _row(**kw):
    return {"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app", "title": "Week 6 lineup: A to FLEX",
            "decision": "A (WR) BE → FLEX", "facts": {"moves": []}, "rationale": None, "player_ids": [1, 2], **kw}


def test_add_decision_round_trips_json_and_orders_newest_first():
    a = db.add_decision(_row())
    b = db.add_decision(_row(at="2026-10-10T13:00:00-04:00", rationale=["followed_call"], followed_call=True))
    rows = db.decisions()
    assert [r["id"] for r in rows] == [b, a]
    assert rows[0]["rationale"] == ["followed_call"] and rows[1]["rationale"] is None
    assert rows[1]["player_ids"] == [1, 2] and rows[1]["facts"] == {"moves": []}
    assert db.decision(a)["title"] == "Week 6 lineup: A to FLEX"
    assert db.decision(9999) is None


def test_update_decision_sets_only_the_fields_given():
    i = db.add_decision(_row())
    after = db.update_decision(i, {"rationale": ["matchup"], "rationale_note": "DAL run D"})
    assert after["rationale"] == ["matchup"] and after["rationale_note"] == "DAL run D" and after["title"].startswith("Week 6")
    assert db.update_decision(9999, {"outcome": "x"}) is None


def test_week_claims_row_updates_in_place_and_keeps_the_rationale():
    first = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", decision="(1) get 1 of 1: A; cut B",
                                       facts={"submits": 1}))
    assert first["needs_rationale"] is True
    db.update_decision(first["id"], {"rationale": ["best_ros"]})
    second = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", decision="(1) get 1 of 1: C; cut B",
                                        facts={"submits": 2}))
    assert second == {"id": first["id"], "needs_rationale": False}
    row = db.decision(first["id"])
    assert row["decision"].endswith("C; cut B") and row["rationale"] == ["best_ros"] and row["facts"] == {"submits": 2}
    assert db.put_week_claims(7, _row(kind="claims", week=7))["id"] != first["id"]


def test_week_claims_row_is_per_plan_generation():
    # after Wednesday's run the week's plan is retired and a new one starts (same ESPN week): its claims are a new decision
    ran = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", facts={"submits": 1, "gen": 0}))
    db.update_decision(ran["id"], {"rationale": ["best_ros"], "outcome": "won A"})
    fresh = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", decision="(1) get 1 of 1: C; cut B",
                                       facts={"submits": 1, "gen": 1}))
    assert fresh["id"] != ran["id"] and fresh["needs_rationale"] is True
    assert db.decision(ran["id"])["outcome"] == "won A" and db.decision(ran["id"])["facts"]["gen"] == 0
    again = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", facts={"submits": 2, "gen": 1}))
    assert again == {"id": fresh["id"], "needs_rationale": True}
    assert len([r for r in db.decisions() if r["kind"] == "claims"]) == 2


def test_none_in_a_json_column_is_sql_null():
    i = db.add_decision(_row(rationale=None, facts=None, player_ids=None))
    with db.engine().connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM decisions WHERE rationale IS NULL AND id = :i"), {"i": i}).scalar() == 1
        assert c.execute(sa.text("SELECT count(*) FROM decisions WHERE facts IS NULL AND player_ids IS NULL AND id = :i"),
                         {"i": i}).scalar() == 1
    assert db.decision(i)["rationale"] is None


def test_projection_rows_upsert_and_read_back():
    n = db.put_projection_rows(5, "week", [{"player_id": 1, "source": "cbs", "points": 12.0},
                                           {"player_id": 1, "source": "espn", "points": 14.0}])
    assert n == 2
    db.put_projection_rows(5, "week", [{"player_id": 1, "source": "cbs", "points": 13.0}])
    rows = db.projection_rows("week")
    assert {(r["player_id"], r["source"]): r["points"] for r in rows} == {(1, "cbs"): 13.0, (1, "espn"): 14.0}
    assert db.projection_rows("ros") == []
    assert db.put_projection_rows(5, "week", []) == 0


def test_source_weights_are_inactive_until_activated():
    a = db.put_source_weights({"week": {"QB": {"espn": 0.5, "cbs": 0.5}}}, fitted_at="2026-10-14T07:00:00")
    b = db.put_source_weights({"week": {"QB": {"espn": 0.7, "cbs": 0.3}}}, fitted_at="2026-10-21T07:00:00")
    assert db.active_source_weights() is None
    assert [f["fitted_at"] for f in db.source_weight_fits()] == [b, a]
    assert db.activate_source_weights(a) is True
    assert db.active_source_weights()["week"]["QB"]["espn"] == 0.5
    assert db.activate_source_weights(b) is True
    assert db.active_source_weights()["week"]["QB"]["espn"] == 0.7, "one active row at a time"
    assert db.activate_source_weights("nope") is False


def test_put_box_doc_writes_doc_once_and_moves_fetched_at_every_read():
    assert db.box_doc(5) is None
    assert db.put_box_doc(5, {"a": 1}, "h1", 100.0) is True
    assert db.put_box_doc(5, {"a": 1}, "h1", 200.0) is False   # unchanged: doc untouched
    row = db.box_doc(5)
    assert row == {"doc": {"a": 1}, "hash": "h1", "fetched_at": 200.0, "settled": False, "final": False}
    assert db.put_box_doc(5, {"a": 2}, "h2", 300.0, settled=True, final=True) is True
    assert db.box_doc(5) == {"doc": {"a": 2}, "hash": "h2", "fetched_at": 300.0, "settled": True, "final": True}
    assert db.box_weeks() == {5: {"fetched_at": 300.0, "settled": True, "final": True}}
    db.put_box_doc(5, {"a": 2}, "h2", 400.0)   # the later read decides final, even with the doc unchanged
    assert db.box_weeks() == {5: {"fetched_at": 400.0, "settled": True, "final": False}}


def test_put_box_doc_writes_corrections_in_the_same_transaction():
    c = {"week": 5, "player_id": 7, "team_id": 3, "old": 10.0, "new": 12.5, "at": "2026-10-15T10:00:00-04:00"}
    db.put_box_doc(5, {"a": 1}, "h1", 1.0, corrections=[c])
    assert db.box_corrections() == [c]
    assert db.box_corrections(week=4) == []


def test_expire_live_boxes_zeroes_only_non_final_rows():
    db.put_box_doc(3, {}, "h", 40.0, settled=True, final=True)
    db.put_box_doc(4, {}, "h", 50.0, final=True)   # finished, unsettled: its fetched_at is the hourly throttle
    db.put_box_doc(6, {}, "h", 60.0)
    db.expire_live_boxes()
    assert db.box_weeks() == {3: {"fetched_at": 40.0, "settled": True, "final": True},
                              4: {"fetched_at": 50.0, "settled": False, "final": True},
                              6: {"fetched_at": 0.0, "settled": False, "final": False}}


def test_gap_rows_round_trip():
    db.put_gap_rows(3, {11: [{"week": 3, "pts": 4.0}], 12: []}, 10.0)
    assert db.gap_rows(3, [11, 12, 13]) == {11: [{"week": 3, "pts": 4.0}], 12: []}
    assert db.gap_ids(3) == [11, 12]
    db.put_gap_rows(3, {11: [{"week": 3, "pts": 6.0}]}, 20.0)
    assert db.gap_rows(3, [11]) == {11: [{"week": 3, "pts": 6.0}]}
    db.add_box_corrections([{"week": 3, "player_id": 11, "team_id": None, "old": 4.0, "new": 6.0, "at": "x"}])
    assert db.box_corrections(week=3)[0]["team_id"] is None


def test_draft_inputs_round_trip():
    rows = [{"player_id": 1, "name": "A", "injury": None, "proj": 10.5}]
    db.put_espn_pool({"sc-QB-x": rows, "sc-RB-y": []}, 2026)
    assert db.espn_pool_rows(["sc-QB-x", "sc-RB-y", "sc-TE-z"], 2026) == {"sc-QB-x": rows, "sc-RB-y": []}
    assert db.espn_pool_rows(["sc-QB-x"], 2025) == {}
    db.put_espn_pool({"sc-QB-x": []}, 2026)
    assert db.espn_pool_rows(["sc-QB-x"], 2026) == {"sc-QB-x": []}, "a refetch replaces the rows"

    assert db.sleeper_season_raw(2026) is None
    db.put_sleeper_season(2026, [{"player": {"position": "QB"}, "stats": {"gp": 17}}])
    assert db.sleeper_season_raw(2026)[0]["stats"] == {"gp": 17}

    assert db.adp_feed("2qb", 10, 2026) is None
    db.put_adp_feed("2qb", 10, 2026, [{"key": "a", "adp": 1.5}], {"total_drafts": 3}, fetched_at="t")
    assert db.adp_feed("2qb", 10, 2026) == {"rows": [{"key": "a", "adp": 1.5}], "meta": {"total_drafts": 3},
                                            "fetched_at": "t"}
    assert db.adp_feed("2qb", 12, 2026) is None


def test_migration_imports_the_draft_input_csvs(tmp_path, monkeypatch):
    """0017 takes espn_players.csv as the home pool under its six formats and the two ADP files as its feeds;
    read back, they are the frames the CSVs were."""
    from pathlib import Path
    import pandas as pd
    from alembic import command
    from ffl import adp, config, leagues, players
    data = tmp_path / "data"
    data.mkdir()
    fx = Path(__file__).parent / "fixtures"
    (data / "settings.json").write_text((fx / "home_settings.json").read_text())
    pool = pd.DataFrame({"player_id": [1, 2, 3], "name": ["Q", "R", "K"], "pos": ["QB", "RB", "K"],
                         "team": ["BUF", "DET", "FA"], "proj": [300.0, 300.0, 0.0], "actual": [0.0] * 3,
                         "injury": ["ACTIVE", None, None], "espn_adp": [1.0, 2.0, 0.0], "espn_auction": [5.0] * 3,
                         "espn_rank_ppr": [1, 2, None], "espn_rank_std": [1, 2, None], "on_team_id": [0] * 3,
                         "status": ["FREEAGENT"] * 3})
    pool.to_csv(data / "espn_players.csv", index=False)
    feed = pd.DataFrame({"name": ["Q"], "key": ["q"], "pos": ["QB"], "team": ["BUF"], "bye": [7.0], "adp": [1.5],
                         "adp_rank": [1], "adp_pos_rank": [1]})
    feed.to_csv(data / "adp_2qb.csv", index=False)
    feed.assign(adp=[30.0]).to_csv(data / "adp_1qb.csv", index=False)
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    home = leagues.home()
    want = pd.read_csv(data / "espn_players.csv").assign(on_team_id=0, status="")   # ownership is never imported
    pd.testing.assert_frame_equal(players.pool(home), want)
    stored = db.espn_pool_rows(list(db.league_scoring_keys(home.key).values()), 2026)
    assert not any({"on_team_id", "status"} & set(r) for rows in stored.values() for r in rows)
    pd.testing.assert_frame_equal(adp.load_feed("2qb", 10, 2026), pd.read_csv(data / "adp_2qb.csv"))
    assert adp.load_feed("ppr", 10, 2026).adp[0] == 30.0
    assert db.sleeper_season_raw(2026) is None, "sleeper_players.csv is rescored already: never imported"
    stored = db.espn_pool_rows(list(db.league_scoring_keys(home.key).values()), 2026)
    assert len(stored) == 6, "every position's format gets a row, an empty one included"
