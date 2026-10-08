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


def backup(dest: Path, keep: int = 14, today: date | None = None) -> Path:
    name = f"ffl-{(today or date.today()).isoformat()}.db"
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        src, out = sqlite3.connect(_source()), sqlite3.connect(local)
        try:
            src.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            out.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"backup failed integrity_check: {ok}")
        staged = dest / f".{name}.tmp"
        shutil.copyfile(local, staged)
        os.replace(staged, dest / name)
    for old in sorted(dest.glob("ffl-*.db"))[:-keep]:
        old.unlink()
    return dest / name


def main(argv: list[str]) -> int:
    dest = Path(argv[0]) if argv else Path(os.environ.get("FFL_BACKUP_DIR") or DEFAULT_DEST)
    out = backup(dest)
    print(f"backup: {out} ({out.stat().st_size} bytes)")
    return 0
