"""The season's SQLite file into Postgres, checked: the one-way move off SQLite (ffl-soc.6.1.3).

    uv run python -m ffl.db pgcopy ~/Developer/FFL/data/ffl.db   # into FFL_DB_URL

The file is opened read-only (the API may still be writing it: a WAL reader sees one consistent snapshot) and must
be at SOURCE_REVISION, the last SQLite schema; the destination must already be at Alembic's head. In one transaction
every table of the destination is emptied (the rows its migrations seeded go too), each table is copied from the file
in foreign-key order, every serial and identity sequence is moved past the copied ids, and the row counts are
compared: any shortfall or extra raises CopyError and rolls the whole copy back.
"""
from __future__ import annotations

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


def copy(sqlite_path: Path, *, dest_url: str | None = None) -> dict[str, tuple[int, int]]:
    """The file's rows into the destination, which is emptied first. Returns {table: (source rows, dest rows)};
    raises CopyError (and copies nothing) on a wrong schema at either end or any count that differs."""
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
    if len(argv) != 1:
        print("usage: python -m ffl.db pgcopy <sqlite file>   (into FFL_DB_URL, which must be at head)")
        return 2
    start = time.monotonic()
    try:
        counts = copy(Path(argv[0]).expanduser())
    except (CopyError, db.SchemaError) as e:
        print(f"pgcopy: {e}")
        return 1
    width = max(map(len, counts))
    print(f"{'table':<{width}}  {'source':>8}  {'dest':>8}")
    for name, (s, d) in counts.items():
        print(f"{name:<{width}}  {s:>8}  {d:>8}")
    print(f"pgcopy: {sum(s for s, _ in counts.values())} rows in {len(counts)} tables, all equal, "
          f"{time.monotonic() - start:.1f} s, into {db.display_url(db.url())}")
    return 0
