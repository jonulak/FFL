"""The season's SQLite file into Postgres, checked: the one-way move off SQLite (ffl-soc.6.1.3).

    uv run python -m ffl.db pgcopy ~/Developer/FFL/data/ffl.db   # into FFL_DB_URL
    uv run --env-file .env.rds python -m ffl.db counts postgresql+psycopg:///ffl env   # rows per table, A vs B

`counts` (table_counts / counts_main) is the read-only half on its own, for any two Postgres databases: the
cutover's local-vs-RDS check, or after any restore.

The file is opened read-only (the API may still be writing it: a WAL reader sees one consistent snapshot) and must
be at SOURCE_REVISION, the last SQLite schema; the destination must already be at Alembic's head. In one transaction
every table of the destination is emptied (the rows its migrations seeded go too), each table is copied from the file
in foreign-key order, every serial and identity sequence is moved past the copied ids, and the row counts are
compared: any shortfall or extra raises CopyError and rolls the whole copy back.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import sqlalchemy as sa

from . import db

SOURCE_REVISION = "0024"  # the last SQLite-era head; 0025 on are Postgres's own (transactions.seq)
BATCH = 1000
# columns the destination has and the file cannot: the copy makes them (transactions.seq, the log's rowid order)
DERIVED = {"transactions": {"seq"}}


class CopyError(RuntimeError):
    pass


def _source_engine(path: Path) -> sa.Engine:
    """Read-only on the file: a missing file raises rather than being created, and nothing is ever written to it."""
    uri = f"{path.resolve().as_uri()}?mode=ro"                     # as_uri: spaces, #, ? in the path
    if not path.is_file():
        raise CopyError(f"no SQLite file at {path}")
    return sa.create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, timeout=5),
                            poolclass=sa.pool.NullPool)


def _check_source(c: sa.Connection) -> dict[str, list[str]]:
    """The file's tables and their columns, after checking it is at SOURCE_REVISION and that every column it holds
    has a place in the destination (a column the copy would drop is data lost) and every destination column a
    value (bar the DERIVED ones)."""
    try:
        rev = c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
    except sa.exc.OperationalError:
        rev = None
    if rev != SOURCE_REVISION:
        raise CopyError(f"the SQLite file is at schema {rev!r}; pgcopy maps {SOURCE_REVISION!r}, the last SQLite "
                        "schema: upgrade the file with the SQLite-era code first")
    ins = sa.inspect(c)
    have = {t for t in ins.get_table_names() if t != "alembic_version" and not t.startswith("sqlite_")}
    want = set(db.metadata.tables)
    if have != want:
        raise CopyError(f"table sets differ: only in the file {sorted(have - want)}, "
                        f"only in the destination {sorted(want - have)}")
    cols = {}
    for name in sorted(want):
        src = [col["name"] for col in ins.get_columns(name)]
        dest = set(db.metadata.tables[name].c.keys())
        extra, missing = set(src) - dest, dest - set(src) - DERIVED.get(name, set())
        if extra or missing:
            raise CopyError(f"{name}: columns only in the file {sorted(extra)}, only in the destination "
                            f"{sorted(missing)}")
        cols[name] = src
    return cols


def _dest_engine(dest_url: str | None) -> sa.Engine:
    eng = sa.create_engine(db._check_url(dest_url or db.url(), "the destination URL"),
                           poolclass=sa.pool.NullPool, json_serializer=db._json_dumps)
    from alembic.runtime.migration import MigrationContext
    with eng.connect() as c:
        current = MigrationContext.configure(c).get_current_revision()
    if current != db._head():
        eng.dispose()
        raise CopyError(f"the destination is at schema {current!r}, not head {db._head()!r}: "
                        "run: uv run alembic upgrade head")
    return eng


def _copy_table(src: sa.Connection, dest: sa.Connection, t: sa.Table, cols: list[str]) -> int:
    """Every row of one table, in the file's rowid order. Selected through db.metadata's own columns, so the
    sqlite dialect's result types do the conversions: JSON text parsed (NaN read; the destination engine writes it
    as null), 0/1 to booleans."""
    q = sa.select(*[t.c[n] for n in cols]).order_by(sa.literal_column("rowid"))
    seq = "seq" in DERIVED.get(t.name, ())
    n = 0
    result = src.execute(q)
    while batch := result.fetchmany(BATCH):
        rows = [dict(r._mapping) for r in batch]
        if seq:  # the log is read in insert order, which in the file was rowid's
            for i, r in enumerate(rows, n + 1):
                r["seq"] = i
        try:
            dest.execute(t.insert(), rows)
        except sa.exc.DBAPIError as e:
            raise CopyError(f"{t.name}: rows {n + 1}..{n + len(rows)} refused: {e.orig}") from e
        n += len(rows)
    return n


def _advance_sequences(dest: sa.Connection) -> None:
    """Every serial and identity sequence past its column's copied max: the next insert gets a fresh id."""
    for t in db.metadata.sorted_tables:
        for col in t.c:
            seq = dest.execute(sa.select(sa.func.pg_get_serial_sequence(f'public."{t.name}"', col.name))).scalar()
            if seq:
                top = dest.execute(sa.select(sa.func.coalesce(sa.func.max(col), 0))).scalar()
                dest.execute(sa.select(sa.func.setval(seq, top + 1, False)))


def copy(sqlite_path: Path, *, dest_url: str | None = None, to_rds: bool = False) -> dict[str, tuple[int, int]]:
    """The file's rows into the destination, which is emptied first. Returns {table: (source rows, dest rows)};
    raises CopyError (and copies nothing) on a wrong schema at either end or any count that differs, or on a
    destination that is not a local server (an RDS, even through the loopback tunnel) without to_rds."""
    from ffl import db_backup
    target = db._check_url(dest_url or db.url(), "the destination URL")
    if not to_rds and not db_backup.is_local(target):
        raise CopyError(f"the destination {db.display_url(target)} is not a local server (or is an RDS through a "
                        "tunnel): pgcopy empties it first; pass --to-rds to copy there")
    src_eng, dest_eng = _source_engine(Path(sqlite_path)), None
    try:
        with src_eng.connect() as src:
            cols = _check_source(src)
            dest_eng = _dest_engine(dest_url)
            src.exec_driver_sql("BEGIN")       # one read snapshot of the file for every table and count
            tables = db.metadata.sorted_tables  # parents first
            with dest_eng.begin() as dest:
                dest.exec_driver_sql("TRUNCATE " + ", ".join(f'"{t.name}"' for t in tables)
                                     + " RESTART IDENTITY CASCADE")
                for t in tables:
                    _copy_table(src, dest, t, cols[t.name])
                _advance_sequences(dest)
                counts = {t.name: (src.execute(sa.select(sa.func.count()).select_from(t)).scalar(),
                                   dest.execute(sa.select(sa.func.count()).select_from(t)).scalar())
                          for t in tables}
                bad = {k: v for k, v in counts.items() if v[0] != v[1]}
                if bad:
                    raise CopyError(f"row counts differ (source, dest): {bad}")  # rolls the copy back
            src.rollback()
        return counts
    finally:
        src_eng.dispose()
        if dest_eng is not None:
            dest_eng.dispose()


def main(argv: list[str]) -> int:
    args = list(argv)
    to_rds = "--to-rds" in args
    if to_rds:
        args.remove("--to-rds")
    if len(args) != 1:
        print("usage: python -m ffl.db pgcopy <sqlite file> [--to-rds]   (into FFL_DB_URL, which must be at head; "
              "a non-local or RDS destination needs --to-rds)")
        return 2
    start = time.monotonic()
    try:
        counts = copy(Path(args[0]).expanduser(), to_rds=to_rds)
    except (CopyError, db.SchemaError, SystemExit) as e:
        print(f"pgcopy: {e}")
        return 1
    width = max(map(len, counts))
    print(f"{'table':<{width}}  {'source':>8}  {'dest':>8}")
    for name, (s, d) in counts.items():
        print(f"{name:<{width}}  {s:>8}  {d:>8}")
    print(f"pgcopy: {sum(s for s, _ in counts.values())} rows in {len(counts)} tables, all equal, "
          f"{time.monotonic() - start:.1f} s, into {db.display_url(db.url())}")
    return 0


COUNTS_USAGE = ("usage: python -m ffl.db counts A [B]   (each a postgresql+psycopg:// URL, or `env` for FFL_DB_URL "
                "from the environment, e.g. `uv run --env-file .env.rds ...`; read-only; exit 1 when A and B differ)")


def table_counts(db_url: str) -> dict[str, int]:
    """Every public table's row count in one database ({table: rows}, alembic_version included), read-only and from
    one snapshot (a repeatable-read transaction). The tables are the server's, not the code's: a table on one side
    only is a difference."""
    eng = db._read_only_engine(db_url)
    try:
        with eng.connect().execution_options(isolation_level="REPEATABLE READ") as c:
            names = c.execute(sa.text("select tablename from pg_tables where schemaname = 'public' "
                                      "order by tablename")).scalars().all()
            out = {n: c.execute(sa.text(f'select count(*) from public."{n}"')).scalar() for n in names}
            c.rollback()
        return out
    finally:
        eng.dispose()


def _counts_url(arg: str) -> str:
    if arg == "env":
        u = os.environ.get("FFL_DB_URL")
        if not u:
            raise db.SchemaError("counts env: FFL_DB_URL is not set in the environment")
        return db._check_url(u, "FFL_DB_URL")
    return db._check_url(arg, "the database URL")


def counts_main(argv: list[str]) -> int:
    """`ffl.db counts A [B]`: every table's rows side by side (one URL: its counts alone); 1 on any difference or
    an unreadable database, 2 on a usage error. Passwords are masked in what it prints."""
    if len(argv) not in (1, 2):
        print(COUNTS_USAGE)
        return 2
    try:
        urls = [_counts_url(a) for a in argv]
        sides = [table_counts(u) for u in urls]
    except (db.SchemaError, sa.exc.DBAPIError) as e:
        print(f"counts: {getattr(e, 'orig', None) or e}")
        return 1
    names = sorted(set().union(*sides))
    width = max(map(len, names), default=5)
    heads = "AB"[:len(urls)]
    for h, u in zip(heads, urls):
        print(f"{h}: {db.display_url(u)}")
    print(f"{'table':<{width}}  " + "  ".join(f"{h:>8}" for h in heads))
    bad = []
    for n in names:
        vals = [s.get(n) for s in sides]
        differs = len(vals) == 2 and vals[0] != vals[1]
        if differs:
            bad.append(n)
        print(f"{n:<{width}}  " + "  ".join(f"{'-' if v is None else v:>8}" for v in vals)
              + ("  <- differs" if differs else ""))
    if len(sides) == 1:
        print(f"counts: {sum(sides[0].values())} rows in {len(names)} tables")
        return 0
    if bad:
        print(f"counts: {len(bad)} of {len(names)} tables differ: {', '.join(bad)}")
        return 1
    print(f"counts: all {len(names)} tables equal ({sum(sides[0].values())} rows each)")
    return 0
