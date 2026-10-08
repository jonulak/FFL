"""A consistent, checked snapshot of the SQLite DB into a synced folder (launchd: com.jonulak.ffl-backup).

sqlite3's online backup is safe while the API writes. The copy is made in a local temp dir and
integrity-checked there, then copied into the destination under a dot-name and renamed, so the sync
client never uploads half a file. Keeps the newest `keep` snapshots. FFL_BACKUP_DIR overrides the folder.

    uv run python -m ffl.db backup [dest_dir]
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from . import db

DEFAULT_DEST = Path.home() / "Library/CloudStorage/GoogleDrive-admin@example.com/My Drive/FFL/backup"


def _source() -> Path:
    u = db.url()
    if not u.startswith("sqlite:///"):
        raise SystemExit(f"backup is for SQLite; {u} needs its own")
    return Path(u.removeprefix("sqlite:///"))


def _check_head(src: sqlite3.Connection) -> None:
    """An empty or stale file passes integrity_check; 14 of them would prune every real snapshot."""
    try:
        row = src.execute("select version_num from alembic_version").fetchone()
    except sqlite3.OperationalError:
        row = None  # no alembic_version table: never migrated
    current, head = row[0] if row else None, db._head()
    if current != head:
        raise RuntimeError(f"backup source is at schema {current!r}, code wants {head!r}: refusing to publish it")


def backup(dest: Path, keep: int = 14, today: date | None = None) -> Path:
    name = f"ffl-{(today or date.today()).isoformat()}.db"
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        # read-only: a missing DB must raise, not be created empty and published as tonight's snapshot
        src = sqlite3.connect(f"file:{_source()}?mode=ro", uri=True, timeout=5)
        out = sqlite3.connect(local)
        try:
            _check_head(src)
            src.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            out.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"backup failed integrity_check: {ok}")
        staged = dest / f".{name}.tmp"
        try:
            shutil.copyfile(local, staged)
            os.replace(staged, dest / name)
        except BaseException:
            staged.unlink(missing_ok=True)  # never leave a half file for the sync client
            raise
    for old in sorted(dest.glob("ffl-*.db"))[:-keep]:
        old.unlink()
    return dest / name


def main(argv: list[str]) -> int:
    dest = Path(argv[0]) if argv else Path(os.environ.get("FFL_BACKUP_DIR") or DEFAULT_DEST)
    out = backup(dest)
    print(f"backup: {out} ({out.stat().st_size} bytes)")
    return 0
