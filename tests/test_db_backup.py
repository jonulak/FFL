import sqlite3
from datetime import date

import pytest

from ffl import db, db_backup


def test_backup_is_a_checked_copy(tmp_path):
    db.add_transactions([{"id": "1-6-DROPPED-1", "date": "d", "type": "DROPPED", "team": "t", "player": "p",
                          "action": "DROPPED", "bid": 0, "reasoning": ""}])
    out = db_backup.backup(tmp_path / "bk", today=date(2026, 10, 8))
    assert out.name == "ffl-2026-10-08.db"
    con = sqlite3.connect(out)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("select count(*) from transactions").fetchone()[0] == 1
    assert not list((tmp_path / "bk").glob(".*")), "no temp file left in the synced folder"


def test_backup_prunes_to_newest_keep(tmp_path):
    bk = tmp_path / "bk"
    for d in range(1, 6):
        db_backup.backup(bk, keep=3, today=date(2026, 10, d))
    assert sorted(p.name for p in bk.glob("ffl-*.db")) == ["ffl-2026-10-03.db", "ffl-2026-10-04.db", "ffl-2026-10-05.db"]


def test_backup_same_day_replaces(tmp_path):
    db_backup.backup(tmp_path, today=date(2026, 10, 8))
    db_backup.backup(tmp_path, today=date(2026, 10, 8))
    assert [p.name for p in tmp_path.glob("ffl-*.db")] == ["ffl-2026-10-08.db"]


def test_backup_of_a_missing_db_raises_and_publishes_nothing(tmp_path, monkeypatch):
    missing = tmp_path / "gone" / "ffl.db"
    (tmp_path / "gone").mkdir()
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{missing}")
    bk = tmp_path / "bk"
    with pytest.raises(sqlite3.OperationalError):
        db_backup.backup(bk, today=date(2026, 10, 8))
    assert not list(bk.glob("ffl-*.db"))
    assert not missing.exists(), "the source must not be created"


def test_backup_failed_publish_leaves_no_temp_file(tmp_path, monkeypatch):
    def boom(*a):
        raise OSError("sync folder gone")
    monkeypatch.setattr(db_backup.os, "replace", boom)
    with pytest.raises(OSError):
        db_backup.backup(tmp_path / "bk", today=date(2026, 10, 8))
    assert not list((tmp_path / "bk").glob("*")) and not list((tmp_path / "bk").glob(".*"))
