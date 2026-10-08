"""The season's shared state in one SQL database (SQLAlchemy Core; Alembic owns the schema).

The only module that talks SQL. Every function takes and returns plain dicts, so season.py stays
pure. SQLite in WAL mode by default (`<DATA>/ffl.db`); FFL_DB_URL points anywhere else (tests, a
later Postgres). Writes are one short transaction each, BEGIN IMMEDIATE on SQLite so a second
writer waits on busy_timeout instead of failing on a lock upgrade.

    uv run alembic upgrade head          # create / migrate the schema
    uv run python -m ffl.db import       # one-time: the old journal/ and data/ files in (idempotent)
    uv run python -m ffl.db backup       # a checked snapshot into the backup dir (launchd nightly)
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import event

from .config import DATA, ROOT, SEASON

metadata = sa.MetaData()


class SchemaError(RuntimeError):
    pass


DBError = sa.exc.SQLAlchemyError  # callers outside db.py catch DB errors through this


def _season():
    return sa.Column("season", sa.Integer, nullable=False, default=SEASON)


transactions_table = sa.Table(  # not `transactions`: that name is the reader below
    "transactions", metadata, _season(),
    sa.Column("id", sa.String, nullable=False),          # `{epoch ms}-{team_id}-{action}-{player_id}`, as journal.py keys it
    sa.Column("date", sa.String, nullable=False),        # ISO minutes, local
    sa.Column("type", sa.String, nullable=False),
    sa.Column("team", sa.String, nullable=False),
    sa.Column("player", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer),                  # parsed from id; None if malformed
    sa.Column("action", sa.String, nullable=False),
    sa.Column("bid", sa.Integer, nullable=False, default=0),
    sa.Column("reasoning", sa.String, nullable=False, default=""),
    sa.PrimaryKeyConstraint("season", "id"),
)
sa.Index("ix_transactions_player", transactions_table.c.season, transactions_table.c.player_id)

draft_picks = sa.Table(
    "draft_picks", metadata, _season(),
    sa.Column("overall", sa.Integer, nullable=False),
    sa.Column("round", sa.Integer, nullable=False),
    sa.Column("pick", sa.Integer, nullable=False),
    sa.Column("team_id", sa.Integer, nullable=False),
    sa.Column("team", sa.String, nullable=False, default=""),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("player", sa.String, nullable=False, default=""),
    sa.Column("pos", sa.String),
    sa.Column("nfl", sa.String),
    sa.Column("adp_2qb", sa.Float),
    sa.Column("board_rank_at_pick", sa.Float),
    sa.Column("proj", sa.Float),
    sa.Column("ts", sa.String),
    sa.PrimaryKeyConstraint("season", "overall"),
)

results = sa.Table(
    "results", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("opponent", sa.String, nullable=False),
    sa.Column("my_score", sa.Float, nullable=False),
    sa.Column("opp_score", sa.Float, nullable=False),
    sa.Column("result", sa.String(1), nullable=False),
    sa.PrimaryKeyConstraint("season", "week"),
)

plays = sa.Table(
    "plays", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("team_id", sa.Integer),
    sa.Column("total", sa.Float, nullable=False),
    sa.Column("body", sa.JSON, nullable=False),          # the whole row as plays.events_from made it
    sa.UniqueConstraint("season", "week", "player_id", "at", "total", name="uq_plays_event"),
)
sa.Index("ix_plays_week_team", plays.c.season, plays.c.week, plays.c.team_id)

outlooks = sa.Table(
    "outlooks", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),        # outlook.ROSTER ("week") / outlook.WAIVERS ("waivers-week")
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("entry", sa.JSON, nullable=False),         # hash, card_hash, bullets, generated_at, error
    sa.Column("updated", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week", "kind", "player_id"),
)

calibration_snapshots = sa.Table(
    "calibration_snapshots", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("k", sa.Float, nullable=False),
    sa.Column("taken_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week"),
)

calibration_rows = sa.Table(
    "calibration_rows", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("pos", sa.String, nullable=False),
    sa.Column("model", sa.Float, nullable=False),
    sa.Column("l3", sa.Float),
    sa.Column("weight", sa.Float, nullable=False),
    sa.Column("games_left", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("season", "week", "player_id"),
    sa.ForeignKeyConstraint(["season", "week"], ["calibration_snapshots.season", "calibration_snapshots.week"]),
)

sigma_fits = sa.Table(
    "sigma_fits", metadata, _season(),
    sa.Column("fitted_at", sa.String, nullable=False),
    sa.Column("fit", sa.JSON, nullable=False),           # {"sigma": {...}, "n": {...}, "weeks": [...]} as write_sigma wrote it
    sa.PrimaryKeyConstraint("season", "fitted_at"),
)

claim_plans = sa.Table(
    "claim_plans", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("plan", sa.JSON, nullable=False),
    sa.Column("updated", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week"),
)


def url() -> str:
    return os.environ.get("FFL_DB_URL") or f"sqlite:///{DATA / 'ffl.db'}"


_ENGINE: sa.Engine | None = None


def _alembic_config():
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _head() -> str:
    from alembic.script import ScriptDirectory
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


def _make_engine() -> sa.Engine:
    eng = sa.create_engine(url())
    if eng.dialect.name == "sqlite":
        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _rec):
            dbapi_conn.isolation_level = None  # we issue BEGIN ourselves (see "begin" below)
            cur = dbapi_conn.cursor()
            # busy_timeout first: the WAL switch needs a lock, and without a timeout a second connector fails at once
            for p in ("busy_timeout=5000", "journal_mode=WAL","foreign_keys=ON", "synchronous=NORMAL"):
                cur.execute(f"PRAGMA {p}")
            cur.close()

        @event.listens_for(eng, "begin")
        def _begin(conn):
            conn.exec_driver_sql("BEGIN IMMEDIATE" if conn.info.get("write") else "BEGIN")
    return eng


def engine() -> sa.Engine:
    """The process's engine; the first call checks the schema is at Alembic's head."""
    global _ENGINE
    if _ENGINE is None:
        eng = _make_engine()
        from alembic.runtime.migration import MigrationContext
        with eng.connect() as c:
            current = MigrationContext.configure(c).get_current_revision()
        if current != _head():
            eng.dispose()
            raise SchemaError(f"{url()} is at schema {current!r}, code wants {_head()!r}: run: uv run alembic upgrade head")
        _ENGINE = eng
    return _ENGINE


def reset() -> None:
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None


@contextmanager
def _write():
    with engine().connect() as c:
        c.info["write"] = True
        try:
            with c.begin():
                yield c
        finally:
            c.info.pop("write", None)


@contextmanager
def _read():
    with engine().connect() as c:
        yield c


def _upsert(table: sa.Table, rows: list[dict], keys: list[str], update: list[str] | None):
    if engine().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table).values(rows)
    if update is None:
        return stmt.on_conflict_do_nothing(index_elements=keys)
    return stmt.on_conflict_do_update(index_elements=keys, set_={c: stmt.excluded[c] for c in update})


def add_plays(rows: list[dict], season: int = SEASON) -> int:
    """Insert the play-feed rows not already stored (key: season, week, player, at, total). Returns how
    many were new."""
    if not rows:
        return 0
    vals = [{"season": season, "week": r["week"], "at": r["at"], "player_id": r["player_id"],
             "team_id": r.get("team_id"), "total": r["total"], "body": r} for r in rows]
    with _write() as c:
        stmt = _upsert(plays, vals, ["season", "week", "player_id", "at", "total"], None).returning(plays.c.id)
        return len(c.execute(stmt).all())


def plays_rows(week: int, season: int = SEASON) -> list[dict]:
    """One week's play-feed rows, oldest first."""
    q = sa.select(plays.c.body).where(plays.c.season == season, plays.c.week == week).order_by(plays.c.id)
    with _read() as c:
        return list(c.execute(q).scalars())


def _tx_player_id(tx_id: str) -> int | None:
    parts = tx_id.split("-", 3)
    try:
        return int(parts[3]) if len(parts) == 4 else None
    except ValueError:
        return None


_TX_COLS = ("id", "date", "type", "team", "player", "player_id", "action", "bid", "reasoning")


def add_transactions(rows: list[dict], season: int = SEASON) -> int:
    """Insert the rows not already logged (key: season, id). Returns how many were new: counted by
    RETURNING, since rowcount is unreliable for a multi-row ON CONFLICT DO NOTHING."""
    if not rows:
        return 0
    vals = [{"season": season, "id": r["id"], "date": r["date"], "type": r["type"], "team": r["team"],
             "player": r["player"], "player_id": _tx_player_id(r["id"]), "action": r["action"],
             "bid": int(r.get("bid") or 0), "reasoning": r.get("reasoning") or ""} for r in rows]
    t = transactions_table
    with _write() as c:
        new = c.execute(_upsert(t, vals, ["season", "id"], None).returning(t.c.id)).all()
    return len(new)


def transactions(season: int = SEASON) -> list[dict]:
    t = transactions_table
    # rowid is SQLite-only (insert order = the CSV's row order); under Postgres order by date, id
    q = sa.select(*[t.c[k] for k in _TX_COLS]).where(t.c.season == season).order_by(sa.text("rowid"))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


_RESULT_COLS = ("week", "opponent", "my_score", "opp_score", "result")


def put_results(rows: list[dict], season: int = SEASON) -> None:
    """Upsert per week. A stale League that knows fewer weeks only rewrites the ones it has."""
    if rows:
        with _write() as c:
            c.execute(_upsert(results, [{"season": season, **r} for r in rows], ["season", "week"],
                              ["opponent", "my_score", "opp_score", "result"]))


def results_rows(season: int = SEASON) -> list[dict]:
    q = sa.select(*[results.c[k] for k in _RESULT_COLS]).where(results.c.season == season).order_by(results.c.week)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


_PICK_COLS = ("overall", "round", "pick", "team_id", "team", "player_id", "player", "pos", "nfl",
              "adp_2qb", "board_rank_at_pick", "proj", "ts")


def put_picks(rows: list[dict], season: int = SEASON) -> None:
    """Upsert per overall pick. team and player are NOT NULL: the watcher can hand over None for a team it
    does not know."""
    if rows:
        rows = [{"season": season, **{k: r.get(k) for k in _PICK_COLS}} for r in rows]
        for r in rows:
            r["team"], r["player"] = r["team"] or "", r["player"] or ""
        with _write() as c:
            c.execute(_upsert(draft_picks, rows, ["season", "overall"], [k for k in _PICK_COLS if k != "overall"]))


def picks(season: int = SEASON) -> list[dict]:
    q = sa.select(*[draft_picks.c[k] for k in _PICK_COLS]).where(draft_picks.c.season == season) \
        .order_by(draft_picks.c.overall)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_outlooks(week: int, kind: str, entries: dict[int, dict], season: int = SEASON) -> None:
    """Upsert each player's entry; never deletes."""
    if not entries:
        return
    from datetime import datetime
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    vals = [{"season": season, "week": week, "kind": kind, "player_id": int(pid), "entry": e, "updated": now}
            for pid, e in entries.items()]
    with _write() as c:
        c.execute(_upsert(outlooks, vals, ["season", "week", "kind", "player_id"], ["entry", "updated"]))


def outlooks_for(week: int, kind: str, season: int = SEASON) -> dict[int, dict]:
    q = sa.select(outlooks.c.player_id, outlooks.c.entry).where(
        outlooks.c.season == season, outlooks.c.week == week, outlooks.c.kind == kind)
    with _read() as c:
        return {pid: e for pid, e in c.execute(q)}


_CAL_COLS = ("player_id", "name", "pos", "model", "l3", "weight", "games_left")


def put_snapshot(week: int, k: float, rows: list[dict], season: int = SEASON) -> bool:
    """The ROS week's snapshot, once: the parent row is DO NOTHING, and rows are written only when it was new."""
    from datetime import datetime
    with _write() as c:
        made = c.execute(_upsert(calibration_snapshots, [{"season": season, "week": week, "k": k,
                                 "taken_at": datetime.now().isoformat(timespec="seconds")}],
                                 ["season", "week"], None).returning(calibration_snapshots.c.week)).all()
        if made and rows:
            c.execute(calibration_rows.insert(), [{"season": season, "week": week, **{x: r[x] for x in _CAL_COLS}}
                                                  for r in rows])
    return bool(made)


def snapshots(season: int = SEASON) -> list[dict]:
    """Every week's snapshot, by week, in the shape the week JSON files had."""
    with _read() as c:
        snaps = [{"week": w, "k": k, "rows": []} for w, k in c.execute(
            sa.select(calibration_snapshots.c.week, calibration_snapshots.c.k)
            .where(calibration_snapshots.c.season == season).order_by(calibration_snapshots.c.week))]
        by_week = {s["week"]: s for s in snaps}
        for r in c.execute(sa.select(calibration_rows).where(calibration_rows.c.season == season)
                           .order_by(sa.text("rowid"))):
            by_week[r.week]["rows"].append({x: getattr(r, x) for x in _CAL_COLS})
    return snaps


def put_sigma(fit: dict, season: int = SEASON, fitted_at: str | None = None) -> None:
    from datetime import datetime
    with _write() as c:
        c.execute(sigma_fits.insert().values(season=season, fit=fit,
                                             fitted_at=fitted_at or datetime.now().isoformat(timespec="seconds")))


def latest_sigma(season: int = SEASON) -> dict | None:
    q = sa.select(sigma_fits.c.fit).where(sigma_fits.c.season == season).order_by(sigma_fits.c.fitted_at.desc()).limit(1)
    with _read() as c:
        return c.execute(q).scalar()


def put_claim_plan(plan: dict, season: int = SEASON) -> None:
    """The week's waiver claim plan, replaced whole (upsert by week)."""
    from datetime import datetime
    with _write() as c:
        c.execute(_upsert(claim_plans, [{"season": season, "week": plan["week"], "plan": plan,
                                         "updated": datetime.now().isoformat(timespec="seconds")}],
                          ["season", "week"], ["plan", "updated"]))


def claim_plan(week: int, season: int = SEASON) -> dict | None:
    q = sa.select(claim_plans.c.plan).where(claim_plans.c.season == season, claim_plans.c.week == week)
    with _read() as c:
        return c.execute(q).scalar()


_KEYS = {"transactions": ["season", "id"], "draft_picks": ["season", "overall"], "results": ["season", "week"],
         "plays": ["season", "week", "player_id", "at", "total"],
         "outlooks": ["season", "week", "kind", "player_id"], "sigma_fits": ["season", "fitted_at"],
         "claim_plans": ["season", "week"]}


def insert_new(table_name: str, rows: list[dict]) -> int:
    """Insert the rows not already present (DO NOTHING on the table's key); returns how many were new.
    Rows carry their own season. For the file import, which must never overwrite."""
    if not rows:
        return 0
    t = metadata.tables[table_name]
    pk = list(t.primary_key.columns)[0]
    with _write() as c:
        return len(c.execute(_upsert(t, rows, _KEYS[table_name], None).returning(pk)).all())


def count(table_name: str, season: int = SEASON) -> int:
    t = metadata.tables[table_name]
    with _read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(t).where(t.c.season == season)).scalar()


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "import":
        from . import db_import
        from .config import JOURNAL
        return db_import.main(JOURNAL, DATA, SEASON)
    if cmd == "backup":
        from . import db_backup
        return db_backup.main(argv[1:])
    print("usage: python -m ffl.db import | backup [dest_dir]")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
