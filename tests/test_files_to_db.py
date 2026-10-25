"""ffl-csz.1: the last app files (push-tap trace, replay draft logs, props trial) live in the DB; 0019 imports them."""
import importlib.util
import json

import pandas as pd
import pytest
import sqlalchemy as sa

from ffl import config, db

ROOT = config.ROOT

FFC_CSV = ("overall,round,pick,team_id,player_id,player,pos,nfl,human\n"
           "1,1,1,1,4429795,Jahmyr Gibbs,RB,DET,False\n"
           "2,1,2,2,3918298,Josh Allen,QB,BUF,True\n"
           "3,2,1,2,0,Nobody Known,WR,,True\n"
           "4,2,2,1,4426515,Puka Nacua,WR,LAR,False\n")
PRACTICE_CSV = ("overall,round,pick,team_id,team,player_id,player,pos,nfl,adp_2qb,board_rank_at_pick,proj,ts\n"
                "1,1,1,6,Team 6,3918298,Josh Allen,QB,BUF,1.9,4.0,361.0,2026-09-07T06:16:15\n"
                "2,1,2,1,Henry's Team,4429795,Jahmyr Gibbs,RB,DET,,1.0,348.5,2026-09-07T06:16:18\n")


def _migration():
    spec = importlib.util.spec_from_file_location("m0019", ROOT / "migrations/versions/0019_files_to_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample(tmp_path):
    """A data dir and a journal holding one file of each kind 0019 imports (and a few it must pass over)."""
    data, journal = tmp_path / "data", tmp_path / "journal"
    (data / "ffc").mkdir(parents=True)
    (data / "props" / "raw" / "week-4-sun").mkdir(parents=True)
    (journal / "draft").mkdir(parents=True)
    (data / "trace.jsonl").write_text(json.dumps({"at": "2026-10-14T17:33:12.123-04:00", "event": "probe",
                                                  "from": "land check"}) + "\nnot json\n"
                                      + json.dumps({"at": "2026-10-14T17:37:01.424-04:00", "event": "hello"}) + "\n")
    (data / "ffc" / "18507075.csv").write_text(FFC_CSV)
    (journal / "draft" / "practice-8-slot4.csv").write_text(PRACTICE_CSV)
    (journal / "draft" / "recap.md").write_text("# not a log\n")
    (journal / "draft" / "notes.csv").write_text("a,b\n1,2\n")                  # a CSV, not a pick log
    snap = {"week": 4, "kind": "sun", "taken_at": "2026-09-28T15:30:00+00:00", "events": 1, "credits_left": 400,
            "rows": [{"player_id": 1, "name": "A", "proj": 10.0, "proj_props": 12.0, "proj3": 11.0}]}
    (data / "props" / "week-4-sun.json").write_text(json.dumps(snap))
    (data / "props" / "raw" / "week-4-sun" / "abc123.json").write_text(json.dumps({"id": "abc123", "bookmakers": []}))
    # junk a deploy must pass over: a truncated snapshot, a truncated raw event, a non-UTF-8 trace line
    (data / "props" / "week-5-thu.json").write_text('{"week": 5, "rows": [')
    (data / "props" / "raw" / "week-4-sun" / "broken.json").write_text("{")
    with open(data / "trace.jsonl", "ab") as f:
        f.write(b"\xff\xfe junk\n")
    return data, journal, snap


def test_migration_imports_each_file_kind_once(tmp_path, monkeypatch):
    from alembic import command
    data, journal, snap = _sample(tmp_path)
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(config, "JOURNAL", journal)
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    assert db.trace_rows() == [{"at": "2026-10-14T17:33:12.123-04:00", "event": "probe", "from": "land check"},
                               {"at": "2026-10-14T17:37:01.424-04:00", "event": "hello"}]
    logs = {(r["source"], r["log_id"]): r for r in db.draft_logs_rows()}
    assert set(logs) == {("ffc", "18507075"), ("practice", "practice-8-slot4")}
    assert logs[("practice", "practice-8-slot4")]["rows"][1]["adp_2qb"] is None
    assert logs[("practice", "practice-8-slot4")]["meta"] == {"file": str(journal / "draft" / "practice-8-slot4.csv")}
    assert db.props_snapshot_docs() == [snap]
    assert db.props_raw_docs(4, "sun") == {"abc123": {"id": "abc123", "bookmakers": []}}
    eng = sa.create_engine(url)
    with eng.begin() as c:
        again = _migration().import_files(c)
    eng.dispose()
    assert again == {"traces": 0, "draft_logs": 0, "props_snapshots": 0, "props_raw": 0}
    assert len(db.trace_rows()) == 2 and len(db.draft_logs_rows()) == 2
    assert (data / "trace.jsonl").exists() and (data / "ffc" / "18507075.csv").exists(), "the files stay"


def test_migration_without_files_imports_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "none")
    monkeypatch.setattr(config, "JOURNAL", tmp_path / "none")
    with db._write() as c:
        assert _migration().import_files(c) == {"traces": 0, "draft_logs": 0, "props_snapshots": 0, "props_raw": 0}


def test_down_to_0018_drops_the_tables_and_up_again(tmp_path, monkeypatch):
    from alembic import command
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "JOURNAL", tmp_path)
    url = f"sqlite:///{tmp_path / 'ud.db'}"
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    command.downgrade(db._alembic_config(), "0018")
    eng = sa.create_engine(url)
    assert not {"traces", "draft_logs", "props_snapshots", "props_raw"} & set(sa.inspect(eng).get_table_names())
    eng.dispose()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    db.add_trace("t", {"event": "x"})
    assert db.trace_rows() == [{"at": "t", "event": "x"}]


def test_draft_log_is_stored_once():
    assert db.put_draft_log("ffc", "1", [{"overall": 1}], {"human": [True]})
    assert not db.put_draft_log("ffc", "1", [{"overall": 9}]), "an id already stored is never replaced"
    assert db.draft_logs_rows("ffc", "1")[0]["rows"] == [{"overall": 1}]
    assert db.draft_log_ids("ffc") == {"1"} and db.draft_log_ids("practice") == set()


def test_props_round_trip():
    assert not db.props_snapshot_exists(5, "thu")
    db.put_props_snapshot(5, "thu", {"week": 5, "kind": "thu", "rows": []})
    db.put_props_snapshot(5, "thu", {"week": 5, "kind": "thu", "rows": [{"player_id": 1}]})   # --force overwrites
    db.put_props_raw(5, "thu", "e1", {"bookmakers": []})
    assert db.props_snapshot_exists(5, "thu")
    assert db.props_snapshot_docs() == [{"week": 5, "kind": "thu", "rows": [{"player_id": 1}]}]
    assert db.props_raw_docs(5, "thu") == {"e1": {"bookmakers": []}} and db.props_raw_docs(5, "sun") == {}


def test_a_csv_log_reads_back_as_the_csv_did(tmp_path):
    """replay's frame from a stored log is the frame pd.read_csv gave it from the file."""
    from ffl import adp, replay
    for name, text in (("ffc.csv", FFC_CSV), ("practice.csv", PRACTICE_CSV)):
        path = tmp_path / name
        path.write_text(text)
        db.put_draft_log("practice", path.stem, adp.records(pd.read_csv(path)))
        pd.testing.assert_frame_equal(replay.load_log(f"practice:{path.stem}"),
                                      pd.read_csv(path).sort_values("overall").reset_index(drop=True))


def test_replay_resolves_stored_logs():
    import pytest
    from ffl import replay
    db.put_draft_log("ffc", "100", [{"overall": 1, "round": 1, "team_id": 1, "player_id": 5, "human": True}])
    db.put_draft_log("ffc", "200", [{"overall": 1, "round": 1, "team_id": 2, "player_id": 6, "human": False}])
    db.put_draft_log("practice", "practice-8-slot4", [{"overall": 1, "round": 1, "team_id": 3, "player_id": 7}])
    db.put_draft_log("practice", "100", [{"overall": 1, "round": 1, "team_id": 4, "player_id": 8}])
    assert replay.resolve_logs(["practice-8-slot4"]) == [("practice", "practice-8-slot4")]
    assert replay.resolve_logs(["ffc:100", "practice:100"]) == [("ffc", "100"), ("practice", "100")]
    assert replay.resolve_logs(["ffc"]) == [("ffc", "100"), ("ffc", "200")], "a bare source is every log in it"
    with pytest.raises(SystemExit, match="ffc:100.*practice:100"):
        replay.resolve_logs(["100"])
    with pytest.raises(SystemExit, match="no stored log"):
        replay.resolve_logs(["ffc:999"])
    assert int(replay.load_log("ffc:200").team_id[0]) == 2


def test_a_practice_draft_is_kept_from_its_own_db(tmp_path, monkeypatch):
    """A practice run's picks live in data-mock/ffl.db, deleted before the next run: --keep copies them into this
    DB's draft_logs as a practice log, read-only on the other file."""
    import shutil

    from ffl import leagues, replay
    other = tmp_path / "mock" / "ffl.db"
    other.parent.mkdir()
    pick ={"overall": 1, "round": 1, "pick": 1, "team_id": 3, "team": "T3", "player_id": 9, "player": "P", "pos": "QB",
            "nfl": "BUF", "adp_2qb": 1.5, "board_rank_at_pick": 2.0, "proj": 300.0, "ts": "2026-09-07T06:16:15"}
    db.put_picks([pick, {**pick, "overall": 2, "team_id": 4, "player_id": 10}], ref=leagues.home())
    db.reset()                                                     # the last close folds the WAL into the file
    shutil.copy(tmp_path / "ffl.db", other)                       # the test's own DB, as a stand-in for data-mock's
    with db._write() as c:
        c.execute(sa.delete(db.draft_picks))
    replay.keep("practice-14-slot2", other, leagues.home().key)
    [log] = db.draft_logs_rows("practice", "practice-14-slot2")
    assert [r["player_id"] for r in log["rows"]] == [9, 10] and log["rows"][0]["adp_2qb"] == 1.5
    assert log["meta"] == {"from": str(other), "league": leagues.home().key}
    with pytest.raises(SystemExit, match="no picks"):
        replay.keep("x", other, "espn:999")


def test_ffc_fetch_stores_new_drafts_and_skips_stored(monkeypatch):
    from ffl import ffc
    page = ('<table><thead><tr><th class="first-col">Rd</th><th>1</th><th class="computer">2</th></tr></thead>'
            '<tbody><tr><td class="first-col">1</td><td class="RB"><a title="Jahmyr Gibbs">J. Gibbs</a> (DET)</td>'
            '<td class="QB"><a title="Josh Allen">J. Allen</a> (BUF)</td></tr></tbody></table>')
    players = pd.DataFrame({"player_id": [4429795, 3918298], "name": ["Jahmyr Gibbs", "Josh Allen"],
                            "pos": ["RB", "QB"], "team": ["DET", "BUF"]})
    asked = []

    class R:
        status_code = 200

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def get(url, **kw):
        asked.append(url)
        return R('<a href="/draft/11">x</a><a href="/draft/22">y</a>' if url == ffc.INDEX else page)

    monkeypatch.setattr(ffc.requests, "get", get)
    monkeypatch.setattr(ffc.board_mod, "load_inputs", lambda: ({}, players, None))
    db.put_draft_log("ffc", "11", [{"overall": 1}])
    assert ffc.fetch(5, pause=0) == [11, 22]
    assert asked == [ffc.INDEX, f"{ffc.BASE}/draft/22"], "a stored draft is never fetched again"
    [row] = db.draft_logs_rows("ffc", "22")
    assert row["rows"] == [{"overall": 1, "round": 1, "pick": 1, "team_id": 1, "player_id": 4429795,
                            "player": "Jahmyr Gibbs", "pos": "RB", "nfl": "DET", "human": True},
                           {"overall": 2, "round": 1, "pick": 2, "team_id": 2, "player_id": 3918298,
                            "player": "Josh Allen", "pos": "QB", "nfl": "BUF", "human": False}]
    assert row["meta"] == {"url": f"{ffc.BASE}/draft/22", "human": [True, False]}


def test_a_failed_upgrade_leaves_the_db_where_it_was(tmp_path, monkeypatch):
    """Migrations run in one transaction, DDL included: a 0019 that fails part way leaves 0018, re-runnable."""
    from alembic import command
    data, journal, _ = _sample(tmp_path)
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(config, "JOURNAL", journal)
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "0018")
    monkeypatch.setattr(config, "DATA", 0)   # Path(0) raises in 0019's import, after its tables are created
    with pytest.raises(TypeError):
        command.upgrade(db._alembic_config(), "head")
    eng = sa.create_engine(url)
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == "0018"
        assert not sa.inspect(c).has_table("traces"), "the new tables went with the failed run"
    eng.dispose()
    monkeypatch.setattr(config, "DATA", data)
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    assert len(db.trace_rows()) == 2
