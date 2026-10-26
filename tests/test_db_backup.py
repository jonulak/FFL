from datetime import date

import pytest
import sqlalchemy as sa

from ffl import db, db_backup


def _count(url, table):
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with eng.connect() as c:
            return c.execute(sa.text(f'select count(*) from "{table}"')).scalar()
    finally:
        eng.dispose()


def _tx():
    db.add_transactions([{"id": "1-6-DROPPED-1", "date": "d", "type": "DROPPED", "team": "t", "player": "p",
                          "action": "DROPPED", "bid": 0, "reasoning": ""}])


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


def test_restore_round_trip(tmp_path, empty_db):
    _tx()
    bk = str(tmp_path / "bk")
    db_backup.backup(bk, today=date(2026, 10, 8))
    fresh = empty_db()
    db_backup.restore(bk, fresh)  # a directory: the newest dump
    for t in ("transactions", "leagues", "strategies"):
        assert _count(fresh, t) == _count(db.url(), t) > 0
    assert _count(fresh, "alembic_version") == 1


def test_restore_named_file_and_newest_pick(tmp_path, empty_db):
    bk = tmp_path / "bk"
    old = db_backup.backup(str(bk), today=date(2026, 10, 1))
    _tx()
    db_backup.backup(str(bk), today=date(2026, 10, 2))
    a = empty_db()
    db_backup.restore(old, a)
    assert _count(a, "transactions") == 0
    b = empty_db()
    db_backup.restore(str(bk), b)
    assert _count(b, "transactions") == 1


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://u:p@db.abc.rds.amazonaws.com:5432/ffl",
    "postgresql+psycopg://u@10.0.0.5/ffl",
])
def test_restore_refuses_a_non_local_host_without_to_rds(tmp_path, url):
    with pytest.raises(SystemExit, match="--to-rds"):
        db_backup.restore(str(tmp_path), url)


@pytest.mark.parametrize("url", ["postgresql+psycopg:///x", "postgresql+psycopg://u@localhost/x",
                                 "postgresql+psycopg://u@127.0.0.1:5432/x", "postgresql+psycopg://u@[::1]/x",
                                 "postgresql+psycopg://u@/x?host=/tmp"])
def test_local_hosts(url, monkeypatch):
    monkeypatch.setattr(db_backup, "server_is_rds", lambda u: False)
    assert db_backup.is_local(url)


def test_a_tunnel_to_the_rds_is_not_local(tmp_path, monkeypatch):
    """127.0.0.1:5434 is the production RDS through the ffl-rds tunnel: the server's answer decides."""
    monkeypatch.setattr(db_backup, "server_is_rds", lambda u: True)
    url = "postgresql+psycopg://ffl:pw@127.0.0.1:5434/ffl?sslmode=require"
    assert not db_backup.is_local(url)
    with pytest.raises(SystemExit, match="--to-rds"):
        db_backup.restore(str(tmp_path), url)


def test_server_is_rds_asks_the_server():
    assert db_backup.server_is_rds(db.url()) is False  # the test server: a plain Postgres, no rds_superuser


def test_server_is_rds_unreachable_is_never_local():
    with pytest.raises(SystemExit, match="cannot ask"):
        db_backup.is_local("postgresql+psycopg://u:p@127.0.0.1:1/x?connect_timeout=2")


def test_s3_store_through_a_stub_client():
    class Stub:
        def __init__(self):
            self.objs = {}

        def upload_file(self, path, bucket, key):
            self.objs[key] = open(path, "rb").read()

        def download_file(self, bucket, key, path):
            open(path, "wb").write(self.objs[key])

        def delete_object(self, Bucket, Key):
            del self.objs[Key]

        def list_objects_v2(self, Bucket, Prefix, **kw):
            return {"Contents": [{"Key": k} for k in sorted(self.objs) if k.startswith(Prefix)], "IsTruncated": False}

    stub = Stub()
    for d in range(1, 5):
        out = db_backup.backup("s3://bkt/ffl/", keep=2, today=date(2026, 10, d), client=stub)
    assert out == "s3://bkt/ffl/ffl-2026-10-04.dump"
    assert sorted(stub.objs) == ["ffl/ffl-2026-10-03.dump", "ffl/ffl-2026-10-04.dump"]


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
    with pytest.raises(SystemExit, match="--url-from-env"):  # both: ambiguous, refused
        db_backup.restore_main(["somewhere", "--url", "postgresql+psycopg:///x", "--url-from-env"])
    with pytest.raises(SystemExit, match="--url"):
        db_backup.restore_main(["somewhere", "--url"])


def test_restore_cli_url_from_env_takes_ffl_db_url(monkeypatch, capsys):
    got = []
    monkeypatch.setattr(db_backup, "restore", lambda src, url, to_rds: got.append((src, url, to_rds)))
    monkeypatch.setenv("FFL_DB_URL", "postgresql+psycopg://ffl:s3cret@127.0.0.1:5434/ffl?sslmode=require")
    assert db_backup.restore_main(["d.dump", "--url-from-env", "--to-rds"]) == 0
    assert got == [("d.dump", "postgresql+psycopg://ffl:s3cret@127.0.0.1:5434/ffl?sslmode=require", True)]
    out = capsys.readouterr().out
    assert "'ffl' on 127.0.0.1" in out and "from the environment" in out and "s3cret" not in out
    monkeypatch.delenv("FFL_DB_URL")
    with pytest.raises(SystemExit, match="not set"):
        db_backup.restore_main(["d.dump", "--url-from-env"])
    assert len(got) == 1


def test_restore_cli_url_from_env_keeps_the_rds_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(db_backup, "server_is_rds", lambda u: True)
    monkeypatch.setenv("FFL_DB_URL", "postgresql+psycopg://ffl:pw@127.0.0.1:5434/ffl")
    with pytest.raises(SystemExit, match="--to-rds"):
        db_backup.restore_main([str(tmp_path), "--url-from-env"])


def test_counts_side_by_side_equal_then_differs(tmp_path, empty_db, capsys, monkeypatch):
    """`ffl.db counts A B`: the cutover's check. A restored copy is equal (0); a row more on one side, or a table
    on one side only, is a difference (1)."""
    _tx()
    bk = str(tmp_path / "bk")
    db_backup.backup(bk, today=date(2026, 10, 8))
    fresh = empty_db()
    db_backup.restore(bk, fresh)
    monkeypatch.setenv("FFL_DB_URL", db.url())
    assert db.main(["counts", "env", fresh]) == 0
    out = capsys.readouterr().out
    assert "all" in out and "equal" in out and "transactions" in out

    db.add_transactions([{"id": "1-6-DROPPED-2", "date": "d", "type": "DROPPED", "team": "t", "player": "q",
                          "action": "DROPPED", "bid": 0, "reasoning": ""}])
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
