"""Checked pg_dump backups of the Postgres DB, to a directory or S3, and the restore (launchd: com.jonulak.ffl-backup).

`pg_dump -Fc` of FFL_DB_URL goes to a local temp file and is checked there (`pg_restore --list` reads it, and its
alembic_version equals the code's head: an empty or stale DB would otherwise push 14 real dumps out), then is put at
`ffl-YYYY-MM-DD.dump` in the destination (a directory, written under a dot-name and renamed so a sync client never
uploads half a file; or `s3://bucket/prefix/`), keeping the newest `keep`. A dump opens with pg_restore anywhere:
no WAL to copy first.

Restore into an empty or disposable database with the API stopped: --clean's DROPs wait on live queries, and
objects the dump does not hold stay.

    uv run python -m ffl.db backup [dest]    # dest: FFL_BACKUP_DEST, else the Drive folder (Mac)
    uv run python -m ffl.db restore <src> --url URL [--to-rds]   # src: a .dump, a directory or s3:// prefix (newest)
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

DEFAULT_DEST = str(Path.home() / "Library/CloudStorage/GoogleDrive-admin@example.com/My Drive/FFL/backup")
_NAME = re.compile(r"^ffl-\d{4}-\d{2}-\d{2}\.dump$")
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


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


class S3Store:
    """s3://bucket/prefix/. `client` is a boto3 S3 client (tests pass a stub); credentials are boto3's own chain."""

    def __init__(self, bucket: str, prefix: str, client=None):
        self.bucket, self.prefix = bucket, (prefix.strip("/") + "/") if prefix.strip("/") else ""
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3")
        return self._client

    def uri(self, name: str) -> str:
        return f"s3://{self.bucket}/{self.prefix}{name}"

    def put(self, local: Path, name: str) -> None:
        self.client.upload_file(str(local), self.bucket, self.prefix + name)

    def list(self) -> list[str]:
        names, token = [], None
        while True:
            kw = {"ContinuationToken": token} if token else {}
            r = self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, **kw)
            names += [o["Key"][len(self.prefix):] for o in r.get("Contents", [])]
            token = r.get("NextContinuationToken") if r.get("IsTruncated") else None
            if not token:
                return sorted(n for n in names if _NAME.match(n))

    def get(self, name: str, local: Path) -> None:
        self.client.download_file(self.bucket, self.prefix + name, str(local))

    def delete(self, name: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + name)


def store_for(dest: str, client=None):
    if dest.startswith("s3://"):
        bucket, _, prefix = dest.removeprefix("s3://").partition("/")
        return S3Store(bucket, prefix, client)
    return DirStore(dest)


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


def backup(dest: str | None = None, keep: int = 14, today: date | None = None, client=None) -> str:
    dest = dest or os.environ.get("FFL_BACKUP_DEST") or DEFAULT_DEST
    name = f"ffl-{(today or date.today()).isoformat()}.dump"
    store = store_for(dest, client)
    uri, env = _libpq(db.url())
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        _run("pg_dump", "-Fc", "--no-owner", "--no-acl", "-f", str(local), f"--dbname={uri}", env=env)
        _check(local)
        store.put(local, name)
    for old in store.list()[:-keep]:
        store.delete(old)
    return store.uri(name)


def is_local(url: str) -> bool:
    u = sa.engine.make_url(url)
    host = u.host or u.query.get("host") or ""
    host = host if isinstance(host, str) else host[0]
    return host in _LOCAL_HOSTS or host.startswith("/")  # a socket directory is local


_NEWER_SETTING = re.compile(r'unrecognized configuration parameter "transaction_timeout"')


def _only_newer_settings(stderr: str) -> bool:
    """True when every error pg_restore reports is the PG 17 `SET transaction_timeout` an older server refuses (a
    dump from a newer client than the target: harmless, the setting is a session default)."""
    errors = [ln for ln in stderr.splitlines() if "error:" in ln]
    return bool(errors) and all(_NEWER_SETTING.search(ln) for ln in errors)


def restore(src: str, url: str, to_rds: bool = False, client=None) -> None:
    """The newest dump in a directory / s3 prefix, or the named .dump, into `url` (--clean --if-exists)."""
    if not to_rds and not is_local(url):
        raise SystemExit(f"restore target {sa.engine.make_url(url).host} is not a local server: pass --to-rds to restore there")
    with tempfile.TemporaryDirectory() as tmp:
        if src.endswith(".dump") and not src.startswith("s3://"):
            local = Path(src)
        else:
            if src.endswith(".dump"):
                src, _, name = src.rpartition("/")
                store = store_for(src + "/", client)
            else:
                store = store_for(src, client)
                names = store.list()
                if not names:
                    raise SystemExit(f"no ffl-*.dump in {src}")
                name = names[-1]
            local = Path(tmp) / name
            store.get(name, local)
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


def restore_main(argv: list[str]) -> int:
    args, url, to_rds = list(argv), None, False
    if "--to-rds" in args:
        args.remove("--to-rds")
        to_rds = True
    if "--url" in args:
        i = args.index("--url")
        url = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1 or not url:
        raise SystemExit("usage: python -m ffl.db restore <src> --url URL [--to-rds]  (--url is required: never the app's DB by default)")
    u = sa.engine.make_url(url)
    print(f"restoring {args[0]} into database {u.database!r} on {u.host or u.query.get('host') or 'the local socket'}")
    restore(args[0], url, to_rds)
    print("restored")
    return 0
