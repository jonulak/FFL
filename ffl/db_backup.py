"""Checked pg_dump backups of the Postgres DB, to a directory, and the restore (the stack's cron: `backup /backups`).

`pg_dump -Fc` of FFL_DB_URL goes to a local temp file and is checked there (`pg_restore --list` reads it, and its
alembic_version equals the code's head: an empty or stale DB would otherwise push 14 real dumps out), then is put at
`ffl-YYYY-MM-DD.dump` in the destination (a directory, written under a dot-name and renamed so a sync client never
uploads half a file), keeping the newest `keep`. A dump opens with pg_restore anywhere:
no WAL to copy first.

Restore into an empty or disposable database with the API stopped: --clean's DROPs wait on live queries, and
objects the dump does not hold stay.

    uv run python -m ffl.db backup [dest]    # dest: else FFL_BACKUP_DEST (one is required)
    uv run python -m ffl.db restore <src> --url URL --yes   # src: a .dump or a directory (newest); prints the target first
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

import sqlalchemy as sa

from . import db

_NAME = re.compile(r"^ffl-\d{4}-\d{2}-\d{2}\.dump$")


# ---- the Postgres client tools ---------------------------------------------------------------------------------

def _candidates(name: str) -> list[str]:
    """`name` on PATH, then the versioned installs Homebrew and Debian keep off it, newest first."""
    found = [shutil.which(name)]
    for v in range(20, 12, -1):
        for d in (f"/opt/homebrew/opt/postgresql@{v}/bin", f"/usr/local/opt/postgresql@{v}/bin",
                  f"/usr/lib/postgresql/{v}/bin"):
            found.append(str(Path(d) / name))
    return [p for p in dict.fromkeys(found) if p and os.access(p, os.X_OK)]


def _tool_major(path: str) -> int:
    out = subprocess.run([path, "--version"], capture_output=True, text=True, check=True).stdout
    m = re.search(r"(\d+)(?:\.\d+)*", out.split(")", 1)[-1])
    if not m:
        raise RuntimeError(f"cannot read the version of {path}: {out!r}")
    return int(m.group(1))


def _server_major(url: str) -> int:
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool)
    try:
        with eng.connect() as c:
            return int(c.execute(sa.text("show server_version_num")).scalar()) // 10000
    finally:
        eng.dispose()


def _libpq(url: str) -> tuple[str, dict[str, str]]:
    """A libpq connection URI with the password moved into PGPASSWORD (argv is visible to every process)."""
    u = sa.engine.make_url(url).set(drivername="postgresql")
    env = {"PGPASSWORD": u.password} if u.password else {}
    return u.set(password=None).render_as_string(hide_password=False), env


def pg_tool(name: str, *args: str, url: str | None = None, env: dict[str, str] | None = None, check: bool = True):
    """Run pg_dump / pg_restore, the first install whose major is >= the server's (a newer client reads an older
    server; an older one refuses a newer)."""
    server = _server_major(url or db.url())
    cands = _candidates(name)
    if not cands:
        raise SystemExit(f"{name} not found on PATH or under Homebrew/Debian's postgresql@N: install the Postgres "
                         f"client tools (brew install postgresql@{server})")
    # the smallest major that qualifies: a newer client's dump carries settings (SET transaction_timeout, PG 17) an older server refuses
    fit = sorted((p for p in cands if _tool_major(p) >= server), key=_tool_major)
    if fit:
        # pg_restore: the newest, since it must also read the archive's own version (a dump made by 18 is unreadable by 16)
        return subprocess.run([fit[-1] if name == "pg_restore" else fit[0], *args], capture_output=True, text=True, check=check,
                              env={**os.environ, **(env or {})})
    have = ", ".join(f"{p} ({_tool_major(p)})" for p in cands)
    raise SystemExit(f"every {name} found is older than the server (Postgres {server}): {have}")


def _run(name: str, *args: str, url: str | None = None, env: dict[str, str] | None = None):
    try:
        return pg_tool(name, *args, url=url, env=env)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"{name} failed ({e.returncode}): {(e.stderr or '').strip()[-2000:]}") from None


# ---- stores ----------------------------------------------------------------------------------------------------

class DirStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def uri(self, name: str) -> str:
        return str(self.path / name)

    def put(self, local: Path, name: str) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        staged = self.path / f".{name}.tmp"
        try:
            shutil.copyfile(local, staged)
            os.replace(staged, self.path / name)
        except BaseException:
            staged.unlink(missing_ok=True)  # never leave a half file for the sync client
            raise

    def list(self) -> list[str]:
        return sorted(p.name for p in self.path.iterdir() if _NAME.match(p.name)) if self.path.is_dir() else []

    def get(self, name: str, local: Path) -> None:
        shutil.copyfile(self.path / name, local)

    def delete(self, name: str) -> None:
        (self.path / name).unlink(missing_ok=True)


# ---- backup / restore -------------------------------------------------------------------------------------------

def dump_head(path: str | Path) -> str | None:
    """The alembic_version row inside a dump (its one table, restored to stdout)."""
    out = _run("pg_restore", "--data-only", "-t", "alembic_version", "-f", "-", str(path)).stdout
    m = re.search(r"COPY [^\n]*alembic_version[^\n]*FROM stdin;\n([^\n]*)\n\\\.", out)
    return m.group(1) if m else None


def _check(path: Path) -> None:
    _run("pg_restore", "--list", str(path))
    current, head = dump_head(path), db._head()
    if current != head:
        raise RuntimeError(f"backup source is at schema {current!r}, code wants {head!r}: refusing to publish it")


def backup(dest: str | None = None, keep: int = 14, today: date | None = None) -> str:
    dest = dest or os.environ.get("FFL_BACKUP_DEST")
    if not dest:
        raise SystemExit("backup: name a directory (python -m ffl.db backup DIR) or set FFL_BACKUP_DEST")
    name = f"ffl-{(today or date.today()).isoformat()}.dump"
    store = DirStore(dest)
    uri, env = _libpq(db.url())
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        _run("pg_dump", "-Fc", "--no-owner", "--no-acl", "-f", str(local), f"--dbname={uri}", env=env)
        _check(local)
        store.put(local, name)
    for old in store.list()[:-keep]:
        store.delete(old)
    return store.uri(name)


_NEWER_SETTING = re.compile(r'unrecognized configuration parameter "transaction_timeout"')


def _only_newer_settings(stderr: str) -> bool:
    """True when every error pg_restore reports is the PG 17 `SET transaction_timeout` an older server refuses (a
    dump from a newer client than the target: harmless, the setting is a session default)."""
    errors = [ln for ln in stderr.splitlines() if "error:" in ln]
    return bool(errors) and all(_NEWER_SETTING.search(ln) for ln in errors)


def restore(src: str, url: str, yes: bool = False) -> None:
    """The newest dump in a directory, or the named .dump, into `url` (--clean --if-exists). Names the target, then
    refuses (exit 2) unless `yes`: --clean drops every object the dump holds there."""
    u = sa.engine.make_url(url)
    print(f"restoring {src} into database {u.database!r} on {u.host or u.query.get('host') or 'the local socket'}")
    if not yes:
        print("restore: pass --yes to replace that database's contents")
        raise SystemExit(2)
    with tempfile.TemporaryDirectory() as tmp:
        if src.endswith(".dump"):
            local = Path(src)
        else:
            store = DirStore(src)
            names = store.list()
            if not names:
                raise SystemExit(f"no ffl-*.dump in {src}")
            local = Path(tmp) / names[-1]
            store.get(names[-1], local)
        _run("pg_restore", "--list", str(local), url=url)
        uri, env = _libpq(url)
        r = pg_tool("pg_restore", "--clean", "--if-exists", "--no-owner", "--no-acl", f"--dbname={uri}", str(local),
                    url=url, env=env, check=False)
        if r.returncode and not _only_newer_settings(r.stderr):
            raise RuntimeError(f"pg_restore failed ({r.returncode}): {r.stderr.strip()[-2000:]}")


def main(argv: list[str]) -> int:
    out = backup(argv[0] if argv else None)
    print(f"backup: {out}")
    return 0


RESTORE_USAGE = ("usage: python -m ffl.db restore <src> --url URL [--yes]  (--url is required: the target is named, "
                 "never the app's DB by default; without --yes it prints the target and stops)")


def restore_main(argv: list[str]) -> int:
    args = list(argv)
    yes = "--yes" in args
    if yes:
        args.remove("--yes")
    if "--url" not in args:
        raise SystemExit(RESTORE_USAGE)
    i = args.index("--url")
    if i + 1 >= len(args):
        raise SystemExit(RESTORE_USAGE)
    url = args[i + 1]
    del args[i:i + 2]
    if len(args) != 1:
        raise SystemExit(RESTORE_USAGE)
    restore(args[0], url, yes)
    print("restored")
    return 0
