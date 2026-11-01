"""ffl-soc.6.1.3: `ffl.db pgcopy` copies the season's SQLite file (schema 0024) into a Postgres DB at head, checked."""
import json
import sqlite3

import pytest
import sqlalchemy as sa

from ffl import db, db_copy
from ffl import leagues

HOME = "espn:100000001"
# three log rows whose ids (and dates) run against the order they were logged in: the copy must keep the log's order
TXS = [("1700000000300-7-ADD-30", "2026-09-10T10:00", "Waiver"),
       ("1700000000100-7-DROP-10", "2026-09-12T09:00", "Waiver"),
       ("1700000000200-7-ADD-20", "2026-09-11T08:00", "Free")]


def _source(tmp_path, revision="0024"):
    """A SQLite file shaped as the last SQLite schema (0024): db.metadata less the Postgres-era transactions.seq."""
    path = tmp_path / "ffl.db"
    eng = sa.create_engine(f"sqlite:///{path}")
    meta = sa.MetaData()
    for t in db.metadata.sorted_tables:
        t.to_metadata(meta)
    meta.tables["transactions"]._columns.remove(meta.tables["transactions"].c.seq)
    meta.create_all(eng)
    eng.dispose()
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
    c.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    c.execute("INSERT INTO leagues (key, provider, provider_id, season, name, strategy, added_at) "
              "VALUES (?, 'espn', '100000001', 2026, 'Copied League', NULL, '2026-09-01')", (HOME,))
    c.execute("INSERT INTO leagues (key, provider, provider_id, season, name, added_at) "
              "VALUES ('espn:950458410', 'espn', '950458410', 2026, 'Public', '2026-10-25')")
    for i, (tid, date, typ) in enumerate(TXS):
        c.execute("INSERT INTO transactions (league, season, id, date, type, team, player, player_id, action, bid, "
                  "reasoning) VALUES (?, 2026, ?, ?, ?, 'Team 7', ?, ?, 'ADD', 0, '')",
                  (HOME, tid, date, typ, f"P{i}", int(tid.rsplit("-", 1)[1])))
    # JSON as SQLAlchemy's sqlite dialect wrote it: Python's json.dumps, a NaN a bare token Postgres refuses
    for i in (5, 9, 12):
        c.execute("INSERT INTO plays (id, league, season, week, player_id, at, total, body) "
                  "VALUES (?, ?, 2026, 7, ?, ?, ?, ?)",
                  (i, HOME, 100 + i, f"2026-10-19T13:0{i % 10}", float(i), json.dumps({"delta": i, "x": float("nan")})))
    c.execute("INSERT INTO users (id, email, created_at) VALUES (1, 'me@example.com', '2026-10-27')")
    c.execute("INSERT INTO sits (user_id, league, season, week, player_id, at) "
              "VALUES (1, ?, 2026, 7, 42, '2026-10-18')", (HOME,))
    c.execute("INSERT INTO source_weights (season, fitted_at, fit, active) VALUES (2026, 'a', '{}', 1), (2026, 'b', '{}', 0)")
    c.execute("INSERT INTO notify_state (season, key, value) VALUES (2026, 'source_status', ?)",
              (json.dumps({"news": True, "quiet": [22, 7]}),))
    c.commit()
    c.close()
    return path


def _rows(sql, **kw):
    with db._read() as c:
        return [tuple(r) for r in c.execute(sa.text(sql), kw)]


def test_copy_counts_match(tmp_path):
    counts = db_copy.copy(_source(tmp_path))
    assert set(counts) == set(db.metadata.tables)
    assert all(src == dest for src, dest in counts.values())
    assert counts["transactions"] == (3, 3) and counts["plays"] == (3, 3) and counts["leagues"] == (2, 2)
    assert counts["strategies"] == (0, 0)
    body = _rows("SELECT body FROM plays WHERE id = 5")[0][0]
    assert body == {"delta": 5, "x": None}, "JSON parsed from the file's text, NaN written as null"
    assert _rows("SELECT active FROM source_weights ORDER BY fitted_at") == [(True,), (False,)], "SQLite's 0/1 as booleans"
    assert db.get_state("source_status") == {"news": True, "quiet": [22, 7]}


def test_copy_replaces_seeded_rows(tmp_path):
    assert _rows("SELECT count(*) FROM strategies")[0][0] > 0, "the migrations seeded presets"
    db_copy.copy(_source(tmp_path))
    assert _rows("SELECT key, name FROM leagues ORDER BY key") == [(HOME, "Copied League"), ("espn:950458410", "Public")]
    assert _rows("SELECT count(*) FROM strategies") == [(0,)]
    assert _rows("SELECT count(*) FROM scoring_formats") == [(0,)]


def test_copy_keeps_transaction_order(tmp_path):
    db_copy.copy(_source(tmp_path))
    assert [r["id"] for r in db.transactions(2026, ref=leagues.get(HOME))] == [t[0] for t in TXS]
    assert _rows("SELECT seq FROM transactions ORDER BY seq") == [(1,), (2,), (3,)]


def test_copy_then_insert_gets_fresh_id(tmp_path):
    db_copy.copy(_source(tmp_path))
    assert db.add_plays([{"week": 7, "player_id": 999, "at": "2026-10-19T14:00", "total": 3.0, "delta": 3.0}],
                        2026, ref=leagues.get(HOME)) == 1
    assert max(r[0] for r in _rows("SELECT id FROM plays")) == 13
    db.add_transactions([{"id": "1700000000400-7-ADD-40", "date": "2026-09-13T10:00", "type": "Free",
                          "team": "Team 7", "player": "P3", "action": "ADD"}], 2026, ref=leagues.get(HOME))
    assert [r["id"] for r in db.transactions(2026, ref=leagues.get(HOME))][-1] == "1700000000400-7-ADD-40"
    assert _rows("SELECT max(seq) FROM transactions") == [(4,)]


def test_copy_refuses_wrong_source_revision(tmp_path):
    before = _rows("SELECT key FROM leagues")
    with pytest.raises(db_copy.CopyError, match="0023"):
        db_copy.copy(_source(tmp_path, revision="0023"))
    assert _rows("SELECT key FROM leagues") == before, "nothing touched"


def test_copy_refuses_a_destination_behind_head(tmp_path, monkeypatch, empty_db):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0024")
    db.reset()
    with pytest.raises(db_copy.CopyError, match="head"):
        db_copy.copy(_source(tmp_path), dest_url=url)


def test_copy_refuses_a_source_column_it_would_drop(tmp_path):
    path = _source(tmp_path)
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE sits ADD COLUMN why VARCHAR")
    c.commit()
    c.close()
    with pytest.raises(db_copy.CopyError, match="sits.*why"):
        db_copy.copy(path)


def test_cli_prints_counts_and_exits_1_on_error(tmp_path, capsys):
    assert db.main(["pgcopy", str(_source(tmp_path))]) == 0
    out = capsys.readouterr().out
    assert "transactions" in out and "3" in out
    (tmp_path / "old").mkdir()
    assert db.main(["pgcopy", str(_source(tmp_path / "old", revision="0001"))]) == 1
    assert "0001" in capsys.readouterr().out
