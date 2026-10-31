import json
import multiprocessing as mp

import sqlalchemy as sa
import pytest
from ffl import db
from tests.home import home_league


def test_fixture_db_is_migrated_and_temp():
    assert sa.engine.make_url(db.url()).database.startswith("ffl_w_"), "the test session's database, never a real one"
    with db._read() as c:
        names = set(sa.inspect(c).get_table_names())
    assert {"transactions", "draft_picks", "results", "plays", "outlooks", "calibration_snapshots",
            "calibration_rows", "sigma_fits", "claim_plans", "alembic_version", "box_scores", "gap_games",
            "box_score_corrections"} <= names


def test_engine_is_postgres_with_a_small_pool():
    eng = db.engine()
    assert eng.dialect.name == "postgresql" and eng.dialect.driver == "psycopg"
    assert eng.pool.size() == 5 and eng.pool._max_overflow == 5 and eng.pool._pre_ping


def test_unmigrated_db_raises(monkeypatch, empty_db):
    monkeypatch.setenv("FFL_DB_URL", empty_db())
    db.reset()
    with pytest.raises(db.SchemaError, match="is at schema None.*alembic upgrade head"):
        db.engine()


def test_unset_url_raises_naming_the_variable(monkeypatch):
    monkeypatch.delenv("FFL_DB_URL")
    db.reset()
    with pytest.raises(db.SchemaError, match="FFL_DB_URL is not set"):
        db.engine()


def test_a_sqlite_url_is_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{tmp_path / 'ffl.db'}")
    db.reset()
    with pytest.raises(db.SchemaError, match="postgresql\\+psycopg"):
        db.engine()
    assert not (tmp_path / "ffl.db").exists()


def test_upsert_insert_then_update_then_nothing():
    t = db.results
    row = {"league": "espn:100000001", "season": 2026, "week": 1, "opponent": "A", "my_score": 1.0, "opp_score": 2.0,
           "result": "L"}
    key = ["league", "season", "week"]
    with db._write() as c:
        c.execute(db._upsert(t, [row], key, ["opponent", "my_score", "opp_score", "result"]))
    with db._write() as c:
        c.execute(db._upsert(t, [{**row, "result": "W"}], key, ["result"]))
    with db._write() as c:
        c.execute(db._upsert(t, [{**row, "result": "T"}], key, None))
    with db._read() as c:
        assert c.execute(sa.select(t.c.result)).scalars().all() == ["W"]


def test_write_rolls_back_on_error():
    with pytest.raises(ZeroDivisionError):
        with db._write() as c:
            c.execute(db.results.insert().values(league="espn:100000001", season=2026, week=9, opponent="x", my_score=0,
                                                 opp_score=0, result="T"))
            1 / 0
    with db._read() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.results)).scalar() == 0


TX = {"id": "1788937502756-6-DROPPED-4688380", "date": "2026-09-09T03:05", "type": "DROPPED",
      "team": "henry's Imposing Team", "player": "Cam Ward", "action": "DROPPED", "bid": 0, "reasoning": ""}


def test_transactions_round_trip_and_dedupe():
    assert db.add_transactions([TX], ref=home_league()) == [{**TX, "player_id": 4688380}]
    assert db.add_transactions([TX], ref=home_league()) == []
    [row] = db.transactions(ref=home_league())
    assert row == {**TX, "player_id": 4688380}


def test_transactions_keep_insert_order():
    """The log reads back in insert order (`seq`), not by id or date: a waiver run stamps every claim with one date,
    and ids lead with epoch ms, which need not follow the order ESPN listed the moves in."""
    ids = lambda *ms: [{**TX, "id": f"{m}-6-DROPPED-{m % 1000}"} for m in ms]
    db.add_transactions(ids(1788937502759, 1788937502751, 1788937502755), ref=home_league())
    db.add_transactions(ids(1788937502750, 1788937502758), ref=home_league())
    assert [r["id"].split("-")[0] for r in db.transactions(ref=home_league())] == \
        ["1788937502759", "1788937502751", "1788937502755", "1788937502750", "1788937502758"]


def test_transactions_dst_player_id_negative():
    db.add_transactions([{**TX, "id": "1788937502756-6-DROPPED--16020"}], ref=home_league())
    assert db.transactions(ref=home_league())[0]["player_id"] == -16020


def _sync_worker(url, rows, q):
    import os
    os.environ["FFL_DB_URL"] = url
    from ffl import db as d
    d.reset()
    q.put(len(d.add_transactions(rows, ref=home_league())))


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
    # one batch per transaction: one worker inserts all 50 (the other waits on its keys), the other finds them all
    assert sorted([q.get(timeout=5), q.get(timeout=5)]) == [0, 50]
    assert len(db.transactions(ref=home_league())) == 50


def test_results_upsert_never_deletes_later_weeks():
    w = lambda wk, r: {"week": wk, "opponent": "X", "my_score": 1.0, "opp_score": 0.0, "result": r}
    db.put_results([w(1, "W"), w(2, "W"), w(3, "L")], ref=home_league())
    db.put_results([w(1, "L")], ref=home_league())  # a stale League that only knows week 1
    assert [(r["week"], r["result"]) for r in db.results_rows(ref=home_league())] == [(1, "L"), (2, "W"), (3, "L")]


PICK = {"overall": 1, "round": 1, "pick": 1, "team_id": 6, "team": "henry's Imposing Team", "player_id": 3918298,
        "player": "Josh Allen", "pos": "QB", "nfl": "BUF", "adp_2qb": 1.9, "board_rank_at_pick": 4.0,
        "proj": 360.8, "ts": "2026-09-07T19:00:10"}


def test_picks_keep_every_column_and_upsert():
    db.put_picks([PICK, {**PICK, "overall": 2, "player_id": 4426348, "player": "Jayden Daniels"}], ref=home_league())
    db.put_picks([PICK], ref=home_league())
    assert db.picks(ref=home_league())[0] == PICK and len(db.picks(ref=home_league())) == 2


def test_picks_nullable_numbers():
    db.put_picks([{**PICK, "adp_2qb": None, "board_rank_at_pick": None, "proj": None}], ref=home_league())
    assert db.picks(ref=home_league())[0]["adp_2qb"] is None


def _play(pid, total, at="2026-10-01T20:29:28", team=3):
    return {"at": at, "week": 4, "player_id": pid, "team_id": team, "delta": 0.6, "total": total, "text": "x"}


def test_plays_round_trip_dedupe_and_order():
    assert db.add_plays([_play(1, 3.2), _play(2, 1.0)], ref=home_league()) == 2
    assert db.add_plays([_play(1, 3.2)], ref=home_league()) == 0
    assert [r["player_id"] for r in db.plays_rows(4, ref=home_league())] == [1, 2]
    assert db.plays_rows(5, ref=home_league()) == []


def test_outlooks_upsert_per_player_week():
    db.put_outlooks(5, {1: {"hash": "a", "bullets": ["x"]}})
    db.put_outlooks(5, {1: {"hash": "b", "bullets": ["y"]}, 2: {"hash": "c", "bullets": []}})
    assert db.outlooks_for(5) == {1: {"hash": "b", "bullets": ["y"]}, 2: {"hash": "c", "bullets": []}}
    assert db.outlooks_for(6) == {}


CAL = {"player_id": 1, "name": "A", "pos": "QB", "model": 234.3, "l3": 13.7, "weight": 1.12, "games_left": 14}


def test_snapshot_first_build_of_the_week_stands():
    assert db.put_snapshot(4, 4, [CAL], ref=home_league()) is True
    assert db.put_snapshot(4, 4, [{**CAL, "model": 1.0}, {**CAL, "player_id": 2}], ref=home_league()) is False
    [snap] = db.snapshots(ref=home_league())
    assert snap == {"week": 4, "k": 4, "rows": [CAL]}, "a later build adds nothing, not even new players"


def test_sigma_latest_wins_and_history_kept():
    assert db.latest_sigma() is None
    db.put_sigma({"sigma": {"QB": 7.0}, "n": {"QB": 50}, "weeks": [1, 2]}, fitted_at="2026-09-23T07:00:00")
    db.put_sigma({"sigma": {"QB": 7.7}, "n": {"QB": 73}, "weeks": [1, 2, 3, 4]}, fitted_at="2026-10-07T07:00:00")
    assert db.latest_sigma()["sigma"]["QB"] == 7.7


def test_claim_plan_round_trip_and_replace():
    assert db.claim_plan(6, ref=home_league()) is None
    db.put_claim_plan({"week": 6, "groups": [{"targets": [101], "cuts": [1], "open": 0}], "submits": []}, ref=home_league())
    db.put_claim_plan({"week": 6, "groups": [], "submits": []}, ref=home_league())
    assert db.claim_plan(6, ref=home_league()) == {"week": 6, "groups": [], "submits": []}


def _row(**kw):
    return {"at": "2026-10-10T12:00:00-04:00", "week": 6, "kind": "lineup", "source": "app", "title": "Week 6 lineup: A to FLEX",
            "decision": "A (WR) BE → FLEX", "facts": {"moves": []}, "rationale": None, "player_ids": [1, 2], **kw}


def test_add_decision_round_trips_json_and_orders_newest_first():
    a = db.add_decision(_row(), ref=home_league())
    b = db.add_decision(_row(at="2026-10-10T13:00:00-04:00", rationale=["followed_call"], followed_call=True), ref=home_league())
    rows = db.decisions(ref=home_league())
    assert [r["id"] for r in rows] == [b, a]
    assert rows[0]["rationale"] == ["followed_call"] and rows[1]["rationale"] is None
    assert rows[1]["player_ids"] == [1, 2] and rows[1]["facts"] == {"moves": []}
    assert db.decision(a, ref=home_league())["title"] == "Week 6 lineup: A to FLEX"
    assert db.decision(9999, ref=home_league()) is None


def test_update_decision_sets_only_the_fields_given():
    i = db.add_decision(_row(), ref=home_league())
    after = db.update_decision(i, {"rationale": ["matchup"], "rationale_note": "DAL run D"}, ref=home_league())
    assert after["rationale"] == ["matchup"] and after["rationale_note"] == "DAL run D" and after["title"].startswith("Week 6")
    assert db.update_decision(9999, {"outcome": "x"}, ref=home_league()) is None


def test_week_claims_row_updates_in_place_and_keeps_the_rationale():
    first = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", decision="(1) get 1 of 1: A; cut B",
                                       facts={"submits": 1}), ref=home_league())
    assert first["needs_rationale"] is True
    db.update_decision(first["id"], {"rationale": ["best_ros"]}, ref=home_league())
    second = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", decision="(1) get 1 of 1: C; cut B",
                                        facts={"submits": 2}), ref=home_league())
    assert second == {"id": first["id"], "needs_rationale": False}
    row = db.decision(first["id"], ref=home_league())
    assert row["decision"].endswith("C; cut B") and row["rationale"] == ["best_ros"] and row["facts"] == {"submits": 2}
    assert db.put_week_claims(7, _row(kind="claims", week=7), ref=home_league())["id"] != first["id"]


def test_week_claims_row_is_per_plan_generation():
    # after Wednesday's run the week's plan is retired and a new one starts (same ESPN week): its claims are a new decision
    ran = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", facts={"submits": 1, "gen": 0}), ref=home_league())
    db.update_decision(ran["id"], {"rationale": ["best_ros"], "outcome": "won A"}, ref=home_league())
    fresh = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", decision="(1) get 1 of 1: C; cut B",
                                       facts={"submits": 1, "gen": 1}), ref=home_league())
    assert fresh["id"] != ran["id"] and fresh["needs_rationale"] is True
    assert db.decision(ran["id"], ref=home_league())["outcome"] == "won A" and db.decision(ran["id"], ref=home_league())["facts"]["gen"] == 0
    again = db.put_week_claims(6, _row(kind="claims", title="Week 6 waiver claims", facts={"submits": 2, "gen": 1}), ref=home_league())
    assert again == {"id": fresh["id"], "needs_rationale": True}
    assert len([r for r in db.decisions(ref=home_league()) if r["kind"] == "claims"]) == 2


def test_none_in_a_json_column_is_sql_null():
    i = db.add_decision(_row(rationale=None, facts=None, player_ids=None), ref=home_league())
    with db.engine().connect() as c:
        assert c.execute(sa.text("SELECT count(*) FROM decisions WHERE rationale IS NULL AND id = :i"), {"i": i}).scalar() == 1
        assert c.execute(sa.text("SELECT count(*) FROM decisions WHERE facts IS NULL AND player_ids IS NULL AND id = :i"),
                         {"i": i}).scalar() == 1
    assert db.decision(i, ref=home_league())["rationale"] is None


def test_projection_rows_upsert_and_read_back():
    n = db.put_projection_rows(5, "week", [{"player_id": 1, "source": "cbs", "points": 12.0},
                                           {"player_id": 1, "source": "espn", "points": 14.0}], ref=home_league())
    assert n == 2
    db.put_projection_rows(5, "week", [{"player_id": 1, "source": "cbs", "points": 13.0}], ref=home_league())
    rows = db.projection_rows("week", ref=home_league())
    assert {(r["player_id"], r["source"]): r["points"] for r in rows} == {(1, "cbs"): 13.0, (1, "espn"): 14.0}
    assert db.projection_rows("ros", ref=home_league()) == []
    assert db.put_projection_rows(5, "week", [], ref=home_league()) == 0


def test_upsert_with_a_repeated_key_in_one_call_keeps_the_last_row():
    """Postgres refuses ON CONFLICT DO UPDATE touching a row twice; SQLite let the last win, and so does _upsert."""
    n = db.put_projection_rows(5, "week", [{"player_id": 1, "source": "cbs", "points": 1.0},
                                           {"player_id": 2, "source": "cbs", "points": 7.0},
                                           {"player_id": 1, "source": "cbs", "points": 2.0}], ref=home_league())
    assert n == 3
    rows = db.projection_rows("week", ref=home_league())
    assert [(r["player_id"], r["points"]) for r in sorted(rows, key=lambda r: r["player_id"])] == [(1, 2.0), (2, 7.0)]


def test_upsert_dedupe_keeps_first_seen_order_and_serves_do_nothing_and_returning():
    t = db.projection_rows_table
    rows = [{"a": 1, "b": 1}, {"a": 2, "b": 2}, {"a": 1, "b": 3}]
    stmt = db._upsert(t, [{"league": "l", "season": 1, "week": 1, "kind": "k", "player_id": r["a"], "source": "s",
                           "points": float(r["b"]), "taken_at": "x"} for r in rows],
                      ["league", "season", "week", "kind", "player_id", "source"], None).returning(t.c.player_id, t.c.points)
    with db._write() as c:
        got = c.execute(stmt).all()
    assert [(r.player_id, r.points) for r in got] == [(1, 3.0), (2, 2.0)]


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
    assert db.box_doc(5, ref=home_league()) is None
    assert db.put_box_doc(5, {"a": 1}, "h1", 100.0, ref=home_league()) is True
    assert db.put_box_doc(5, {"a": 1}, "h1", 200.0, ref=home_league()) is False   # unchanged: doc untouched
    row = db.box_doc(5, ref=home_league())
    assert row == {"doc": {"a": 1}, "hash": "h1", "fetched_at": 200.0, "settled": False, "final": False}
    assert db.put_box_doc(5, {"a": 2}, "h2", 300.0, settled=True, final=True, ref=home_league()) is True
    assert db.box_doc(5, ref=home_league()) == {"doc": {"a": 2}, "hash": "h2", "fetched_at": 300.0, "settled": True, "final": True}
    assert db.box_weeks(ref=home_league()) == {5: {"fetched_at": 300.0, "settled": True, "final": True}}
    db.put_box_doc(5, {"a": 2}, "h2", 400.0, ref=home_league())   # the later read decides final, even with the doc unchanged
    assert db.box_weeks(ref=home_league()) == {5: {"fetched_at": 400.0, "settled": True, "final": False}}


def test_put_box_doc_writes_corrections_in_the_same_transaction():
    c = {"week": 5, "player_id": 7, "team_id": 3, "old": 10.0, "new": 12.5, "at": "2026-10-15T10:00:00-04:00"}
    db.put_box_doc(5, {"a": 1}, "h1", 1.0, corrections=[c], ref=home_league())
    assert db.box_corrections(ref=home_league()) == [c]
    assert db.box_corrections(week=4, ref=home_league()) == []


def test_expire_live_boxes_zeroes_only_non_final_rows():
    db.put_box_doc(3, {}, "h", 40.0, settled=True, final=True, ref=home_league())
    db.put_box_doc(4, {}, "h", 50.0, final=True, ref=home_league())   # finished, unsettled: its fetched_at is the hourly throttle
    db.put_box_doc(6, {}, "h", 60.0, ref=home_league())
    db.expire_live_boxes(ref=home_league())
    assert db.box_weeks(ref=home_league()) == {3: {"fetched_at": 40.0, "settled": True, "final": True},
                              4: {"fetched_at": 50.0, "settled": False, "final": True},
                              6: {"fetched_at": 0.0, "settled": False, "final": False}}


def test_gap_rows_round_trip():
    db.put_gap_rows(3, {11: [{"week": 3, "pts": 4.0}], 12: []}, 10.0, ref=home_league())
    assert db.gap_rows(3, [11, 12, 13], ref=home_league()) == {11: [{"week": 3, "pts": 4.0}], 12: []}
    assert db.gap_ids(3, ref=home_league()) == [11, 12]
    db.put_gap_rows(3, {11: [{"week": 3, "pts": 6.0}]}, 20.0, ref=home_league())
    assert db.gap_rows(3, [11], ref=home_league()) == {11: [{"week": 3, "pts": 6.0}]}
    db.add_box_corrections([{"week": 3, "player_id": 11, "team_id": None, "old": 4.0, "new": 6.0, "at": "x"}], ref=home_league())
    assert db.box_corrections(week=3, ref=home_league())[0]["team_id"] is None


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


def test_migration_imports_the_draft_input_csvs(tmp_path, monkeypatch, empty_db):
    """0017 takes espn_players.csv as the home pool under its six formats and the two ADP files as its feeds."""
    from pathlib import Path
    import pandas as pd
    from alembic import command
    from ffl import config
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
    monkeypatch.setenv("FFL_DB_URL", empty_db())
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0017")
    eng = sa.create_engine(db.url())                     # at 0017: db's readers are head's
    with eng.connect() as c:
        pools = {r.scoring_format: r.rows for r in c.execute(sa.text("SELECT scoring_format, rows FROM espn_pools"))}
        feeds = {r.fmt: r.rows for r in c.execute(sa.text("SELECT fmt, rows FROM adp_feeds WHERE teams = 10"))}
        sleeper = c.execute(sa.text("SELECT count(*) FROM sleeper_season")).scalar()
    eng.dispose()
    assert len(pools) == 6, "every position's format gets a row, an empty one included"
    rows = [r for rs in pools.values() for r in rs]
    assert sorted((r["player_id"], r["pos"], r["proj"]) for r in rows) == [(1, "QB", 300.0), (2, "RB", 300.0),
                                                                          (3, "K", 0.0)]
    assert not any({"on_team_id", "status"} & set(r) for r in rows), "ownership is never imported"
    assert [(r["name"], r["adp"]) for r in feeds["2qb"]] == [("Q", 1.5)] and feeds["ppr"][0]["adp"] == 30.0
    assert sleeper == 0, "sleeper_players.csv is rescored already: never imported"


def _other_league():
    from ffl import leagues
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None), user_id=0)


def test_picks_are_kept_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    db.put_picks([{**PICK, "overall": i} for i in (1, 2, 3)], ref=home)
    db.put_picks([{**PICK, "overall": i, "player": "Other Man"} for i in (1, 2)], ref=other)
    assert len(db.picks(ref=home)) == 3 and len(db.picks(ref=other)) == 2
    assert db.picks(ref=home)[0]["player"] == "Josh Allen" and db.picks(ref=other)[0]["player"] == "Other Man"
    assert db.picks(ref=home_league()) == db.picks(ref=home), "no league named is the current (home) one"


def test_draft_feed_and_state_are_kept_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    assert db.draft_feed(other) == ([], 0) and db.draft_state(other) is None
    first = [{"teamId": 3, "playerId": 11, "lineupSlotId": 0, "overall": 1}]
    both = first + [{"teamId": 4, "playerId": 12, "lineupSlotId": 0, "overall": 2}]
    db.put_draft_feed(other, first, 2)
    db.put_draft_feed(other, both, 1)
    db.put_draft_state(other, {"current_pick": 5, "on_clock_is_me": True})
    assert db.draft_feed(other) == (both, 1)
    assert db.draft_state(other) == {"current_pick": 5, "on_clock_is_me": True}
    assert db.draft_feed(home) == ([], 0) and db.draft_state(home) is None


def _at_0017(tmp_path, monkeypatch, empty_db, seed_home: bool):
    """A DB at 0017 holding two draft_picks rows, as the live data/ffl.db stands before 0018."""
    from alembic import command
    from ffl import config
    monkeypatch.setattr(config, "DATA", tmp_path)       # 0015 / 0017 import files from DATA: there are none
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    if seed_home:
        monkeypatch.setenv("LEAGUE_ID", "100000001")
        monkeypatch.setenv("SEASON", "2026")
        monkeypatch.setenv("MY_TEAM_ID", "7")
    else:
        monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "0017")
    eng = sa.create_engine(url)                          # not db.engine(): it refuses a schema short of head
    with eng.begin() as c:
        for r in (PICK, {**PICK, "overall": 2, "player_id": 4426348, "player": "Jayden Daniels"}):
            c.execute(sa.text(f"INSERT INTO draft_picks (season, {', '.join(db._PICK_COLS)}) "
                              f"VALUES (2026, {', '.join(':' + k for k in db._PICK_COLS)})"), r)
    eng.dispose()
    return url


def test_migration_backfills_home_league(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0017(tmp_path, monkeypatch, empty_db, seed_home=True)
    command.upgrade(db._alembic_config(), "0018")
    eng = sa.create_engine(url)
    with eng.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(sa.text("SELECT * FROM draft_picks ORDER BY overall"))]
        pk = sa.inspect(c).get_pk_constraint("draft_picks")["constrained_columns"]
    eng.dispose()
    assert [r["league"] for r in rows] == ["espn:100000001"] * 2
    assert pk == ["league", "season", "overall"]
    assert [{k: r[k] for k in db._PICK_COLS} for r in rows] == \
        [PICK, {**PICK, "overall": 2, "player_id": 4426348, "player": "Jayden Daniels"}]


def test_migration_refuses_picks_with_no_home_league(tmp_path, monkeypatch, empty_db):
    from alembic import command
    _at_0017(tmp_path, monkeypatch, empty_db, seed_home=False)
    with pytest.raises(RuntimeError, match="home league"):
        command.upgrade(db._alembic_config(), "0018")


# 0021: one row in each league-data table at 0020, as the live data/ffl.db stands before it
_SEED = {
    "transactions": [{"season": 2026, "id": "b", "date": "d", "type": "t", "team": "T", "player": "P", "player_id": 1,
                      "action": "a", "bid": 0, "reasoning": ""},
                     {"season": 2026, "id": "a", "date": "d", "type": "t", "team": "T", "player": "P", "player_id": 2,
                      "action": "a", "bid": 0, "reasoning": ""}],
    "results": [{"season": 2026, "week": 1, "opponent": "X", "my_score": 1.0, "opp_score": 0.0, "result": "W"}],
    "plays": [{"id": 7, "season": 2026, "week": 4, "at": "t", "player_id": 1, "team_id": 3, "total": 1.0, "body": "{}"}],
    "box_scores": [{"season": 2026, "week": 4, "doc": "{}", "hash": "h", "fetched_at": 1.0}],
    "gap_games": [{"season": 2026, "week": 3, "player_id": 1, "rows": "[]", "fetched_at": 1.0}],
    "box_score_corrections": [{"id": 11, "season": 2026, "week": 3, "player_id": 1, "team_id": None, "old": 1.0,
                               "new": 2.0, "at": "t"}],
    "outlooks": [{"season": 2026, "week": 5, "kind": "week", "player_id": 1, "entry": "{}", "updated": "t"}],
    "calibration_snapshots": [{"season": 2026, "week": 4, "k": 4.0, "taken_at": "t"}],
    "calibration_rows": [{"season": 2026, "week": 4, "player_id": 1, "name": "A", "pos": "QB", "model": 1.0, "l3": None,
                          "weight": 1.0, "games_left": 10}],
    "projection_rows": [{"season": 2026, "week": 5, "kind": "week", "player_id": 1, "source": "cbs", "points": 1.0,
                         "taken_at": "t"}],
    "claim_plans": [{"season": 2026, "week": 5, "plan": "{}", "updated": "t"}],
    "sits": [{"season": 2026, "week": 8, "player_id": 1, "at": "t"}],
    "seats": [{"season": 2026, "player_id": 1, "seat_id": 2}],
    "watchlist": [{"season": 2026, "player_id": 1, "added_at": "t"}],
    "watch_signals": [{"season": 2026, "key": "k", "player_id": 1, "kind": "usage", "text": "x", "at": "t"}],
    "notices": [{"season": 2026, "key": "waivers:r1", "kind": "waivers", "title": "t", "body": "b", "url": "/",
                 "created_at": "t", "sent_at": None, "outcome": None}],
    "decisions": [{"id": 9, "season": 2026, "at": "t", "week": 6, "kind": "note", "source": "session", "title": "t",
                   "decision": "", "import_key": None}],
}
_PK_0021 = {"transactions": ["league", "season", "id"], "results": ["league", "season", "week"], "plays": ["id"],
            "box_scores": ["league", "season", "week"], "gap_games": ["league", "season", "week", "player_id"],
            "box_score_corrections": ["id"], "outlooks": ["league", "season", "week", "kind", "player_id"],
            "calibration_snapshots": ["league", "season", "week"],
            "calibration_rows": ["league", "season", "week", "player_id"],
            "projection_rows": ["league", "season", "week", "kind", "player_id", "source"],
            "claim_plans": ["league", "season", "week"], "sits": ["league", "season", "week", "player_id"],
            "seats": ["league", "season", "player_id"], "watchlist": ["league", "season", "player_id"],
            "watch_signals": ["league", "season", "key"], "notices": ["league", "season", "key"], "decisions": ["id"]}
_UQ_0021 = {"plays": {"uq_plays_event": ["league", "season", "week", "player_id", "at", "total"]},
            "decisions": {"uq_decisions_import": ["league", "season", "import_key"]}}
_IX_0021 = {"transactions": {"ix_transactions_player": ["league", "season", "player_id"]},
            "plays": {"ix_plays_week_team": ["league", "season", "week", "team_id"]},
            "watch_signals": {"ix_watch_signals_player": ["league", "season", "player_id"]},
            "notices": {"ix_notices_unsent": ["league", "season", "sent_at"]},
            "decisions": {"ix_decisions_week_kind": ["league", "season", "week", "kind"]}}
_IDS = {"plays": [7], "decisions": [9], "box_score_corrections": [11]}


def _at_0020(tmp_path, monkeypatch, empty_db, seed_home: bool = True) -> str:
    """A DB at 0020 holding `_SEED`; with seed_home False, no leagues row at all."""
    from alembic import command
    from ffl import config
    monkeypatch.setattr(config, "DATA", tmp_path)       # 0015 / 0017 / 0019 import files: there are none
    monkeypatch.setattr(config, "JOURNAL", tmp_path)
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0020")
    eng = sa.create_engine(url)                          # not db.engine(): it refuses a schema short of head
    with eng.begin() as c:
        if not seed_home:
            c.execute(sa.text("DELETE FROM leagues"))
        for t, rows in _SEED.items():
            for r in rows:
                cols = ", ".join(f'"{k}"' for k in r)
                c.execute(sa.text(f"INSERT INTO {t} ({cols}) VALUES ({', '.join(':' + k for k in r)})"), r)
    eng.dispose()
    return url


def _schema(url: str) -> dict:
    """Per seeded table: columns, PK, uniques and indexes by name, FKs, row count, leagues and ids."""
    eng = sa.create_engine(url)
    out = {}
    with eng.connect() as c:
        ins = sa.inspect(c)
        for t in _SEED:
            out[t] = {"cols": [x["name"] for x in ins.get_columns(t)],
                      "pk": ins.get_pk_constraint(t)["constrained_columns"],
                      "uq": {u["name"]: u["column_names"] for u in ins.get_unique_constraints(t)},
                      "ix": {i["name"]: i["column_names"] for i in ins.get_indexes(t)
                             if not i.get("duplicates_constraint")},  # Postgres lists a unique key's index too
                      "fk": ins.get_foreign_keys(t),
                      "n": c.execute(sa.text(f"SELECT count(*) FROM {t}")).scalar()}
            if "league" in out[t]["cols"]:
                out[t]["leagues"] = list(c.execute(sa.text(f"SELECT DISTINCT league FROM {t}")).scalars())
            if t in _IDS:
                out[t]["ids"] = list(c.execute(sa.text(f"SELECT id FROM {t} ORDER BY id")).scalars())
    eng.dispose()
    return out


def test_0021_files_every_row_under_home(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0020(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0021")
    s = _schema(url)
    for t, rows in _SEED.items():
        assert s[t]["n"] == len(rows), t
        assert s[t]["leagues"] == ["espn:100000001"], t
        assert s[t]["cols"][0] == "league", t
        assert s[t]["pk"] == _PK_0021[t], t
        assert s[t]["uq"] == _UQ_0021.get(t, {}), t
        assert s[t]["ix"] == _IX_0021.get(t, {}), t
    for t, ids in _IDS.items():
        assert s[t]["ids"] == ids, t
    [fk] = s["calibration_rows"]["fk"]
    assert fk["constrained_columns"] == ["league", "season", "week"]
    assert fk["referred_table"] == "calibration_snapshots"
    assert fk["referred_columns"] == ["league", "season", "week"]


def test_0021_downgrade_keeps_home_rows(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0020(tmp_path, monkeypatch, empty_db)
    before = _schema(url)
    command.upgrade(db._alembic_config(), "0021")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO results (league, season, week, opponent, my_score, opp_score, result) "
                          "VALUES ('espn:999', 2026, 1, 'Y', 0, 0, 'L')"))
    eng.dispose()
    command.downgrade(db._alembic_config(), "0020")
    after = _schema(url)
    for t in _SEED:
        assert after[t]["n"] == len(_SEED[t]), t
        assert after[t]["cols"] == before[t]["cols"] and "league" not in after[t]["cols"], t
        assert after[t]["pk"] == before[t]["pk"] == [c for c in _PK_0021[t] if c != "league"], t
        assert after[t]["uq"] == before[t]["uq"], t
        assert after[t]["ix"] == before[t]["ix"], t
    [fk] = after["calibration_rows"]["fk"]
    assert fk["constrained_columns"] == ["season", "week"] and fk["referred_table"] == "calibration_snapshots"


def test_0021_refuses_rows_with_no_home_league(tmp_path, monkeypatch, empty_db):
    from alembic import command
    _at_0020(tmp_path, monkeypatch, empty_db, seed_home=False)
    with pytest.raises(RuntimeError, match="home league"):
        command.upgrade(db._alembic_config(), "0021")


def test_league_data_is_kept_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    res = lambda opp: [{"week": 1, "opponent": opp, "my_score": 1.0, "opp_score": 0.0, "result": "W"}]
    db.put_results(res("H"), ref=home)
    db.put_results(res("O"), ref=other)
    assert db.results_rows(ref=home)[0]["opponent"] == "H" and db.results_rows(ref=other)[0]["opponent"] == "O"
    assert db.results_rows(ref=home_league()) == db.results_rows(ref=home)

    db.put_sit(8, 1, True, ref=home)
    db.put_sit(8, 2, True, ref=other)
    assert db.sat_ids(8, ref=home) == {1} and db.sat_ids(8, ref=other) == {2} and db.sat_ids(8, ref=home_league()) == {1}
    db.put_sit(8, 2, False, ref=home)                    # standing up home's absent row leaves other's sit alone
    assert db.sat_ids(8, ref=other) == {2}

    db.put_watch(1, True, ref=home)
    db.put_watch(2, True, ref=other)
    assert db.watched_ids(ref=home) == [1] and db.watched_ids(ref=other) == [2] and db.watched_ids(ref=home_league()) == [1]

    db.put_seats({1: 5}, ref=home)
    db.put_seats({1: 6}, ref=other)
    assert db.seat_map(ref=home) == {1: 5} and db.seat_map(ref=other) == {1: 6} and db.seat_map(ref=home_league()) == {1: 5}

    n = {"key": "waivers:r1", "kind": "waivers", "title": "t", "body": "b", "url": "/", "created_at": "t"}
    assert db.add_notices([n], ref=home) == 1 and db.add_notices([n], ref=other) == 1
    db.mark_notices(["waivers:r1"], "sent", "t", ref=other)
    assert [r["key"] for r in db.unsent_notices(ref=home)] == ["waivers:r1"] and db.unsent_notices(ref=other) == []
    assert db.unsent_notices(ref=home_league()) == db.unsent_notices(ref=home)

    assert len(db.add_transactions([TX], ref=home)) == 1 and len(db.add_transactions([TX], ref=other)) == 1
    db.add_transactions([{**TX, "id": "1788937502757-6-DROPPED-1"}], ref=other)
    assert len(db.transactions(ref=home)) == 1 and len(db.transactions(ref=other)) == 2
    assert db.transactions(ref=home_league()) == db.transactions(ref=home)

    db.put_claim_plan({"week": 6, "who": "home"}, ref=home)
    db.put_claim_plan({"week": 6, "who": "other"}, ref=other)
    assert db.claim_plan(6, ref=home)["who"] == "home" and db.claim_plan(6, ref=other)["who"] == "other"
    assert db.claim_plan(6, ref=home_league()) == db.claim_plan(6, ref=home)

    db.put_box_doc(5, {"a": "home"}, "h", 1.0, ref=home)
    db.put_box_doc(5, {"a": "other"}, "o", 2.0, ref=other)
    assert db.box_doc(5, ref=home)["doc"] == {"a": "home"} and db.box_doc(5, ref=other)["doc"] == {"a": "other"}
    assert db.box_doc(5, ref=home_league()) == db.box_doc(5, ref=home)
    db.expire_live_boxes(ref=other)
    assert db.box_weeks(ref=home)[5]["fetched_at"] == 1.0 and db.box_weeks(ref=other)[5]["fetched_at"] == 0.0


def _at_0021(tmp_path, monkeypatch, empty_db, seed_home: bool = True) -> str:
    """A DB at 0021 with notify_state and page_cache seeded (state keys of both kinds, two pages)."""
    from alembic import command
    url = _at_0020(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0021")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        if not seed_home:
            c.execute(sa.text("DELETE FROM leagues"))
        for k, v in (("waiver_run", {"run": 1}), ("news_mutes", [1]), ("source_status", {"cbs": "ok"})):
            c.execute(sa.text("INSERT INTO notify_state (season, key, value) VALUES (2026, :k, :v)"),
                      {"k": k, "v": json.dumps(v)})
        for k in ("roster", "week:6"):
            c.execute(sa.text("INSERT INTO page_cache (season, key, payload, built_at) VALUES (2026, :k, '{}', 1.0)"),
                      {"k": k})
    eng.dispose()
    return url


def _rows(url: str, sql: str) -> list:
    eng = sa.create_engine(url)
    with eng.connect() as c:
        out = [tuple(r) for r in c.execute(sa.text(sql))]
    eng.dispose()
    return out


def test_0022_moves_league_keys_under_home(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0021(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0022")
    assert _rows(url, "SELECT league, season, key FROM league_state") == [("espn:100000001", 2026, "waiver_run")]
    assert sorted(_rows(url, "SELECT key FROM notify_state")) == [("news_mutes",), ("source_status",)]
    assert sorted(_rows(url, "SELECT league, key FROM page_cache")) == [("espn:100000001", "roster"),
                                                                       ("espn:100000001", "week:6")]
    eng = sa.create_engine(url)
    with eng.connect() as c:
        assert sa.inspect(c).get_pk_constraint("page_cache")["constrained_columns"] == ["league", "season", "key"]
        assert sa.inspect(c).get_pk_constraint("league_state")["constrained_columns"] == ["league", "season", "key"]
    eng.dispose()


def test_0022_downgrade_keeps_home(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0021(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0022")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO league_state (league, season, key, value) VALUES ('espn:999', 2026, 'waiver_run', '{}')"))
        c.execute(sa.text("INSERT INTO page_cache (league, season, key, payload, built_at) "
                          "VALUES ('espn:999', 2026, 'other', '{}', 1.0)"))
        c.execute(sa.text("INSERT INTO notify_state (season, key, value) VALUES (2026, 'waiver_run', '{\"run\": \"stale\"}')"))
    eng.dispose()
    command.downgrade(db._alembic_config(), "0021")
    assert _rows(url, "SELECT value FROM notify_state WHERE key = 'waiver_run'") == [({"run": 1},)]
    assert sorted(_rows(url, "SELECT key FROM notify_state")) == [("news_mutes",), ("source_status",), ("waiver_run",)]
    assert sorted(_rows(url, "SELECT key FROM page_cache")) == [("roster",), ("week:6",)]
    assert _rows(url, "SELECT to_regclass('league_state')") == [(None,)]
    eng = sa.create_engine(url)
    with eng.connect() as c:
        ins = sa.inspect(c)
        assert ins.get_pk_constraint("page_cache")["constrained_columns"] == ["season", "key"]
        assert [x["name"] for x in ins.get_columns("page_cache")] == ["season", "key", "payload", "built_at"]
    eng.dispose()


def test_0022_refuses_with_no_home_league(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0021(tmp_path, monkeypatch, empty_db, seed_home=False)
    with pytest.raises(RuntimeError, match="home league"):
        command.upgrade(db._alembic_config(), "0022")
    assert len(_rows(url, "SELECT key FROM notify_state")) == 3, "nothing moved"


def test_league_state_and_pages_kept_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    db.put_league_state("waiver_run", {"who": "home"}, ref=home)
    db.put_league_state("waiver_run", {"who": "other"}, ref=other)
    assert db.get_league_state("waiver_run", ref=home) == {"who": "home"}
    assert db.get_league_state("waiver_run", ref=other) == {"who": "other"}
    assert db.get_league_state("waiver_run", ref=home_league()) == {"who": "home"}, "no league named is home"
    assert db.get_league_state("pending_claims", default=[], ref=other) == []

    db.put_page("roster", {"a": "home"}, 1.0, ref=home)
    db.put_page("roster", {"a": "other"}, 2.0, ref=other)
    db.put_page("week:6", {"a": "home"}, 1.0, ref=home)
    db.put_page("week:6", {"a": "other"}, 2.0, ref=other)
    assert {r["key"]: r["payload"] for r in db.pages(ref=other)}["roster"] == {"a": "other"}
    assert {r["key"]: r["payload"] for r in db.pages(ref=home_league())}["roster"] == {"a": "home"}
    db.drop_page_prefixes(("week",), ref=other)
    assert sorted(r["key"] for r in db.pages(ref=other)) == ["roster"]
    assert sorted(r["key"] for r in db.pages(ref=home)) == ["roster", "week:6"]
    db.drop_pages(["roster"], ref=other)
    assert db.pages(ref=other) == [] and len(db.pages(ref=home)) == 2
    db.put_page("roster", {}, 3.0, ref=other)
    db.clear_pages(ref=other)
    assert db.pages(ref=other) == [] and len(db.pages(ref=home_league())) == 2


def test_decisions_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    i = db.add_decision(_row(), ref=other)
    assert db.decisions(ref=home_league()) == [] and [r["id"] for r in db.decisions(ref=other)] == [i]
    assert db.decision(i, ref=home_league()) is None and db.decision(i, ref=other)["title"].startswith("Week 6")
    assert db.update_decision(i, {"outcome": "x"}, ref=home) is None
    assert db.update_decision(i, {"outcome": "x"}, ref=other)["outcome"] == "x"
    assert "league" not in db.decision(i, ref=other)
    claims = _row(kind="claims", title="Week 6 waiver claims", facts={"gen": 0})
    a, b = db.put_week_claims(6, claims, ref=home), db.put_week_claims(6, claims, ref=other)
    assert a["id"] != b["id"], "one week's claims row per league"


def test_plays_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    assert db.add_plays([_play(1, 3.2)], ref=home) == 1
    assert db.add_plays([_play(1, 3.2)], ref=other) == 1
    assert db.add_plays([_play(2, 1.0)], ref=other) == 1
    assert len(db.plays_rows(4, ref=home)) == 1 and len(db.plays_rows(4, ref=other)) == 2
    db.update_plays([{**_play(1, 3.2), "text": "other's"}], ref=other)
    assert db.plays_rows(4, ref=home)[0]["text"] == "x" and db.plays_rows(4, ref=other)[0]["text"] == "other's"


def test_calibration_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    assert db.put_snapshot(4, 4, [CAL], ref=home) is True
    assert db.put_snapshot(4, 5, [CAL, {**CAL, "player_id": 2}], ref=other) is True
    [snap] = db.snapshots(ref=other)
    assert snap["k"] == 5 and len(snap["rows"]) == 2
    assert db.snapshots(ref=home_league()) == db.snapshots(ref=home) == [{"week": 4, "k": 4, "rows": [CAL]}]


def test_models_match_migration():
    """The template DB is `alembic upgrade head`: every league-data table's columns and keys are db.metadata's."""
    with db._read() as c:
        ins = sa.inspect(c)
        for name in _SEED:
            t = db.metadata.tables[name]
            assert [x["name"] for x in ins.get_columns(name)] == [x.name for x in t.columns], name
            assert ins.get_pk_constraint(name)["constrained_columns"] == [x.name for x in t.primary_key.columns], name
            assert {u["name"]: u["column_names"] for u in ins.get_unique_constraints(name)} == \
                {u.name: [x.name for x in u.columns] for u in t.constraints if isinstance(u, sa.UniqueConstraint)}, name
            assert {i["name"]: i["column_names"] for i in ins.get_indexes(name) if not i.get("duplicates_constraint")} == \
                {i.name: [x.name for x in i.columns] for i in t.indexes}, name
            assert [f["constrained_columns"] for f in ins.get_foreign_keys(name)] == \
                [[x.parent.name for x in f.elements] for f in t.foreign_key_constraints], name


def test_no_league_named_is_home_whatever_ffl_league_says(monkeypatch):
    other = _other_league()
    monkeypatch.setenv("FFL_LEAGUE", other.key)          # a draft shell's export must not move home's season data
    db.put_results([{"week": 1, "opponent": "H", "my_score": 1.0, "opp_score": 0.0, "result": "W"}], ref=home_league())
    db.put_sit(8, 1, True, ref=home_league())
    assert db.results_rows(ref=other) == [] and db.sat_ids(8, ref=other) == set()
    from ffl import leagues
    assert db.results_rows(ref=home_league())[0]["opponent"] == "H" and db.sat_ids(8, ref=home_league()) == {1}


def test_0021_downgrade_refuses_rows_with_no_home_league(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0020(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0021")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM leagues"))
    eng.dispose()
    with pytest.raises(RuntimeError, match="home league"):
        command.downgrade(db._alembic_config(), "0020")
    assert _schema(url)["results"]["n"] == 1, "nothing dropped"


def test_0021_upgrades_an_empty_db_with_no_home_league(tmp_path, monkeypatch, empty_db):
    from alembic import command
    from ffl import config
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "JOURNAL", tmp_path)
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "0020")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("DELETE FROM leagues"))
    eng.dispose()
    command.upgrade(db._alembic_config(), "0021")
    assert _schema(url)["results"]["pk"] == _PK_0021["results"]


def test_state_functions_refuse_the_other_kind():
    with pytest.raises(ValueError):
        db.get_state("waiver_run")
    with pytest.raises(ValueError):
        db.put_state("watch_state", {})
    with pytest.raises(ValueError):
        db.put_league_state("source_status", {}, ref=home_league())
    with pytest.raises(ValueError):
        db.get_league_state("prefs", ref=home_league())


def _at_0022(tmp_path, monkeypatch, empty_db) -> str:
    """A DB at 0022 with two called-back rows (one play, two players)."""
    from alembic import command
    url = _at_0021(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0022")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        for pid in (11, 12):
            c.execute(sa.text("INSERT INTO called_back (season, play_id, player_id, week, body) "
                              "VALUES (2026, 'p1', :pid, 6, '{}')"), {"pid": pid})
    eng.dispose()
    return url


def test_0023_files_called_back_under_home(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0022(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0023")
    assert sorted(_rows(url, "SELECT league, season, play_id, player_id, week FROM called_back")) == [
        ("espn:100000001", 2026, "p1", 11, 6), ("espn:100000001", 2026, "p1", 12, 6)]
    eng = sa.create_engine(url)
    with eng.connect() as c:
        ins = sa.inspect(c)
        assert ins.get_pk_constraint("called_back")["constrained_columns"] == ["league", "season", "play_id", "player_id"]
        assert [i["column_names"] for i in ins.get_indexes("called_back")] == [["league", "season", "week"]]
    eng.dispose()


def test_0023_downgrade_keeps_home(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0022(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0023")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO called_back (league, season, play_id, player_id, week, body) "
                          "VALUES ('espn:999', 2026, 'p1', 11, 6, '{}')"))
    eng.dispose()
    command.downgrade(db._alembic_config(), "0022")
    assert sorted(_rows(url, "SELECT season, play_id, player_id FROM called_back")) == [(2026, "p1", 11), (2026, "p1", 12)]


def test_0024_makes_home_player_views_every_leagues(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = _at_0022(tmp_path, monkeypatch, empty_db)
    command.upgrade(db._alembic_config(), "0023")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        for league, v in (("espn:100000001", '{"who": "home"}'), ("espn:999", '{"who": "other"}')):
            c.execute(sa.text("INSERT INTO league_state (league, season, key, value) VALUES (:l, 2026, 'player_presets', :v)"),
                      {"l": league, "v": v})
    eng.dispose()
    command.upgrade(db._alembic_config(), "0024")
    assert _rows(url, "SELECT season, value FROM notify_state WHERE key = 'player_presets'") == [(2026, {"who": "home"})]
    assert _rows(url, "SELECT league FROM league_state WHERE key = 'player_presets'") == []
    command.downgrade(db._alembic_config(), "0023")
    assert _rows(url, "SELECT league, value FROM league_state WHERE key = 'player_presets'") == [("espn:100000001", {"who": "home"})]
    assert _rows(url, "SELECT key FROM notify_state WHERE key = 'player_presets'") == []


def test_called_back_rows_kept_per_league():
    from ffl import leagues
    home, other = home_league(), _other_league()
    row = {"play_id": "p1", "player_id": 11, "week": 6}
    db.put_called_back([{**row, "who": "home"}], [], ref=home)
    db.put_called_back([{**row, "who": "other"}], [], ref=other)
    assert db.called_back_rows(6, ref=home)[0]["who"] == "home" and db.called_back_rows(6, ref=other)[0]["who"] == "other"
    assert db.called_back_rows(6, ref=home_league()) == db.called_back_rows(6, ref=home)
    db.put_called_back([], [("p1", 11)], ref=other)        # another league dropping its row leaves home's alone
    assert db.called_back_rows(6, ref=other) == [] and len(db.called_back_rows(6, ref=home)) == 1
