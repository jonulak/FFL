from datetime import date

import pytest
import sqlalchemy as sa

from ffl import db, db_backup
from tests.home import home_league


def _count(url, table):
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with eng.connect() as c:
            return c.execute(sa.text(f'select count(*) from "{table}"')).scalar()
    finally:
        eng.dispose()


def _tables(url):
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with eng.connect() as c:
            return c.execute(sa.text("select count(*) from pg_tables where schemaname = 'public'")).scalar()
    finally:
        eng.dispose()


def _tx():
    db.add_transactions([{"id": "1-6-DROPPED-1", "date": "d", "type": "DROPPED", "team": "t", "player": "p",
                          "action": "DROPPED", "bid": 0, "reasoning": ""}], ref=home_league())


def test_backup_needs_a_destination(monkeypatch):
    monkeypatch.delenv("FFL_BACKUP_DEST", raising=False)
    with pytest.raises(SystemExit, match="FFL_BACKUP_DEST"):
        db_backup.backup()


def test_backup_writes_checked_dump(tmp_path):
    _tx()
    out = db_backup.backup(str(tmp_path / "bk"), today=date(2026, 10, 8))
    assert out.endswith("ffl-2026-10-08.dump")
    assert "transactions" in db_backup.pg_tool("pg_restore", "--list", out).stdout
    assert db_backup.dump_head(out) == db._head()
    assert not list((tmp_path / "bk").glob(".*")), "no temp file left in the synced folder"


def test_backup_prunes_to_keep(tmp_path):
    bk = str(tmp_path / "bk")
    for d in range(1, 6):
        db_backup.backup(bk, keep=3, today=date(2026, 10, d))
    assert sorted(p.name for p in (tmp_path / "bk").glob("ffl-*.dump")) == [
        "ffl-2026-10-03.dump", "ffl-2026-10-04.dump", "ffl-2026-10-05.dump"]


def test_backup_same_day_replaces(tmp_path):
    db_backup.backup(str(tmp_path), today=date(2026, 10, 8))
    db_backup.backup(str(tmp_path), today=date(2026, 10, 8))
    assert [p.name for p in tmp_path.glob("ffl-*.dump")] == ["ffl-2026-10-08.dump"]


def test_backup_refuses_wrong_head(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_head", lambda: "9999")
    with pytest.raises(RuntimeError, match="schema"):
        db_backup.backup(str(tmp_path / "bk"), today=date(2026, 10, 8))
    assert not list((tmp_path / "bk").glob("*")), "a refused dump publishes nothing"


def test_backup_unreachable_db_publishes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FFL_DB_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none?connect_timeout=2")
    with pytest.raises(Exception):
        db_backup.backup(str(tmp_path / "bk"), today=date(2026, 10, 8))
    assert not list((tmp_path / "bk").glob("*"))


def test_backup_failed_publish_leaves_no_temp_file(tmp_path, monkeypatch):
    def boom(*a):
        raise OSError("sync folder gone")
    monkeypatch.setattr(db_backup.os, "replace", boom)
    with pytest.raises(OSError):
        db_backup.backup(str(tmp_path / "bk"), today=date(2026, 10, 8))
    assert not list((tmp_path / "bk").glob("*")) and not list((tmp_path / "bk").glob(".*"))


def test_restore_refuses_without_yes(tmp_path, empty_db, capsys):
    _tx()
    bk = str(tmp_path / "bk")
    db_backup.backup(bk, today=date(2026, 10, 8))
    fresh = empty_db()
    with pytest.raises(SystemExit) as e:
        db_backup.restore(bk, fresh)
    assert e.value.code == 2
    out = capsys.readouterr().out
    assert sa.engine.make_url(fresh).database in out and "--yes" in out
    assert _tables(fresh) == 0, "nothing restored"
    with pytest.raises(SystemExit) as e:
        db.main(["restore", bk, "--url", fresh])
    assert e.value.code == 2 and _tables(fresh) == 0


def test_restore_round_trip(tmp_path, empty_db):
    _tx()
    bk = str(tmp_path / "bk")
    db_backup.backup(bk, today=date(2026, 10, 8))
    fresh = empty_db()
    db_backup.restore(bk, fresh, yes=True)  # a directory: the newest dump
    for t in ("transactions", "leagues", "strategies"):
        assert _count(fresh, t) == _count(db.url(), t) > 0
    assert _count(fresh, "alembic_version") == 1


def test_restore_named_file_and_newest_pick(tmp_path, empty_db):
    bk = tmp_path / "bk"
    old = db_backup.backup(str(bk), today=date(2026, 10, 1))
    _tx()
    db_backup.backup(str(bk), today=date(2026, 10, 2))
    a = empty_db()
    db_backup.restore(old, a, yes=True)
    assert _count(a, "transactions") == 0
    b = empty_db()
    db_backup.restore(str(bk), b, yes=True)
    assert _count(b, "transactions") == 1


def test_a_client_older_than_the_server_is_refused(monkeypatch):
    monkeypatch.setattr(db_backup, "_tool_major", lambda path: 14)
    monkeypatch.setattr(db_backup, "_candidates", lambda name: ["/x/pg_dump"])
    monkeypatch.setattr(db_backup, "_server_major", lambda url: 16)
    with pytest.raises(SystemExit, match="older than the server"):
        db_backup.pg_tool("pg_dump", "--version")


def test_a_newer_clients_transaction_timeout_is_the_only_error_tolerated():
    one = ('pg_restore: error: could not execute query: ERROR:  unrecognized configuration parameter '
           '"transaction_timeout"\nCommand was: SET transaction_timeout = 0;\npg_restore: warning: errors ignored on restore: 1')
    assert db_backup._only_newer_settings(one)
    assert not db_backup._only_newer_settings(one + "\npg_restore: error: could not execute query: ERROR:  boom")
    assert not db_backup._only_newer_settings("")


def test_pg_restore_takes_the_newest_client_pg_dump_the_smallest(monkeypatch):
    # a dump made by 18 into a server 16 with both clients installed: 16 cannot read the archive
    monkeypatch.setattr(db_backup, "_server_major", lambda url: 16)
    monkeypatch.setattr(db_backup, "_candidates", lambda name: [f"/pg16/{name}", f"/pg18/{name}"])
    monkeypatch.setattr(db_backup, "_tool_major", lambda path: 16 if "pg16" in path else 18)
    ran = []
    monkeypatch.setattr(db_backup.subprocess, "run", lambda cmd, **kw: ran.append(cmd[0]))
    db_backup.pg_tool("pg_restore", "--list", "x")
    db_backup.pg_tool("pg_dump", "-Fc")
    assert ran == ["/pg18/pg_restore", "/pg16/pg_dump"]


def test_restore_cli_needs_url_and_touches_nothing(monkeypatch):
    monkeypatch.setattr(db_backup, "restore", lambda *a, **k: pytest.fail("restored"))
    with pytest.raises(SystemExit, match="--url"):
        db_backup.restore_main(["somewhere"])
    with pytest.raises(SystemExit, match="--url"):
        db.main(["restore", "somewhere"])
    with pytest.raises(SystemExit, match="--url"):
        db_backup.restore_main(["somewhere", "--url"])


def test_counts_side_by_side_equal_then_differs(tmp_path, empty_db, capsys, monkeypatch):
    """`ffl.db counts A B`: the cutover's check. A restored copy is equal (0); a row more on one side, or a table
    on one side only, is a difference (1)."""
    _tx()
    bk = str(tmp_path / "bk")
    db_backup.backup(bk, today=date(2026, 10, 8))
    fresh = empty_db()
    db_backup.restore(bk, fresh, yes=True)
    monkeypatch.setenv("FFL_DB_URL", db.url())
    assert db.main(["counts", "env", fresh]) == 0
    out = capsys.readouterr().out
    assert "all" in out and "equal" in out and "transactions" in out

    db.add_transactions([{"id": "1-6-DROPPED-2", "date": "d", "type": "DROPPED", "team": "t", "player": "q",
                          "action": "DROPPED", "bid": 0, "reasoning": ""}], ref=home_league())
    assert db.main(["counts", db.url(), fresh]) == 1
    out = capsys.readouterr().out
    assert "transactions" in out.split("differ:")[1] and "leagues" not in out.split("differ:")[1]

    eng = sa.create_engine(fresh, poolclass=sa.pool.NullPool)
    with eng.begin() as c:
        c.execute(sa.text("create table only_here (x int)"))
    eng.dispose()
    assert db.main(["counts", fresh]) == 0  # one URL: its counts alone
    assert "only_here" in capsys.readouterr().out
    assert db.main(["counts", fresh, fresh]) == 0
    capsys.readouterr()
    assert db.main(["counts", db.url(), fresh]) == 1
    assert "only_here" in capsys.readouterr().out.split("differ:")[1]


def test_counts_usage_and_bad_urls(capsys, monkeypatch):
    assert db.main(["counts"]) == 2
    assert db.main(["counts", "a", "b", "c"]) == 2
    assert db.main(["counts", "sqlite:///x.db"]) == 1
    monkeypatch.delenv("FFL_DB_URL")
    assert db.main(["counts", "env"]) == 1
    assert db.main(["counts", "postgresql+psycopg://u:hidden@127.0.0.1:1/x?connect_timeout=2"]) == 1
    assert "hidden" not in capsys.readouterr().out
