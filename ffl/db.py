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
            for p in ("journal_mode=WAL", "busy_timeout=5000", "foreign_keys=ON", "synchronous=NORMAL"):
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
