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
import threading
from contextlib import contextmanager

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

# Each projection source's number per player (STRATEGY.md "Lineup" / "Waivers"): what the consensus read,
# kept so `ffl.calibrate sources` can fit per-source weights. kind "week" (that week's game) or "ros" (the
# ROS week's rest of season); written until his kickoff, then left alone (espn_season.record_projections).
projection_rows_table = sa.Table(  # not `projection_rows`: that name is the reader below
    "projection_rows", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("kind", sa.String, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("points", sa.Float, nullable=False),
    sa.Column("taken_at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week", "kind", "player_id", "source"),
)

# Fitted per-source weights ({"week": {pos: {source: w}}, "ros": {...}, "report": {...}}); none applies
# until one is activated by hand (`ffl.calibrate sources --activate <fitted_at>`).
source_weights = sa.Table(
    "source_weights", metadata, _season(),
    sa.Column("fitted_at", sa.String, nullable=False),
    sa.Column("fit", sa.JSON, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.PrimaryKeyConstraint("season", "fitted_at"),
)

claim_plans = sa.Table(
    "claim_plans", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("plan", sa.JSON, nullable=False),
    sa.Column("updated", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week"),
)

# The players sat by hand for a week (the Roster sheet's toggle): left out of the lineup call. A row is a sit and
# the toggle off deletes it, so a new week starts with none.
sits = sa.Table(
    "sits", metadata, _season(),
    sa.Column("week", sa.Integer, nullable=False),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("at", sa.String, nullable=False),
    sa.PrimaryKeyConstraint("season", "week", "player_id"),
)

# Which row of a starting slot each player stands in (season.reseat): ESPN's two WR slots are one slot to it and it
# lists them in roster-acquisition order, so a swap would jump rows. A player stands in `seat_id`'s place in that
# order; one with no row stands in his own.
seats = sa.Table(
    "seats", metadata, _season(),
    sa.Column("player_id", sa.Integer, nullable=False),
    sa.Column("seat_id", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("season", "player_id"),
)

# Web Push: one row per installed device (the browser's PushSubscription). A 404/410 from the push service deletes it.
push_subscriptions = sa.Table(
    "push_subscriptions", metadata,
    sa.Column("endpoint", sa.String, primary_key=True),
    sa.Column("keys", sa.JSON, nullable=False),          # {p256dh, auth}
    sa.Column("ua", sa.String, nullable=False, default=""),
    sa.Column("created_at", sa.String, nullable=False),
)

# The notification outbox (ffl/notify.py): every detector inserts if absent on key, so an event is one notice however
# often it is seen, across restarts. sent_at stays NULL until the flush delivers it or marks it off/expired.
notices = sa.Table(
    "notices", metadata, _season(),
    sa.Column("key", sa.String, nullable=False),         # move:<tx id> / injury:<week>:<pid>:<status> / waivers:<run>
    sa.Column("kind", sa.String, nullable=False),        # move / trade / injury / waivers
    sa.Column("title", sa.String, nullable=False),
    sa.Column("body", sa.String, nullable=False),
    sa.Column("url", sa.String, nullable=False),
    sa.Column("created_at", sa.String, nullable=False),
    sa.Column("sent_at", sa.String),
    sa.Column("outcome", sa.String),                     # sent / off / expired
    sa.PrimaryKeyConstraint("season", "key"),
)
sa.Index("ix_notices_unsent", notices.c.season, notices.c.sent_at)

# notify's prefs and detector memory (last waiver run, the pending-claims snapshot), one JSON value per key.
notify_state = sa.Table(
    "notify_state", metadata, _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("value", sa.JSON, nullable=False),
    sa.PrimaryKeyConstraint("season", "key"),
)

# The decision journal (was journal/DECISIONS.md): every lineup move, sit, claims submit and free add the app makes,
# and what sessions record through `python -m ffl.decisions`. rationale NULL = still needs one (the Decisions page
# lists it as open); a list of preset keys (ffl.decisions.PRESETS) otherwise. import_key is set only on rows the
# one-time import made from the markdown file (`<heading>#<n>`), so re-running it inserts nothing.
decisions_table = sa.Table(
    "decisions", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    _season(),
    sa.Column("at", sa.String, nullable=False),          # ISO seconds with offset (app/session); YYYY-MM-DD (import)
    sa.Column("week", sa.Integer),
    sa.Column("kind", sa.String, nullable=False),        # lineup|sit|unsit|claims|free_add|trade|draft|note
    sa.Column("source", sa.String, nullable=False),      # app|session|import
    sa.Column("title", sa.String, nullable=False),
    sa.Column("decision", sa.String, nullable=False, default=""),
    sa.Column("facts", sa.JSON(none_as_null=True)),
    sa.Column("rationale", sa.JSON(none_as_null=True)),
    sa.Column("rationale_note", sa.String),
    sa.Column("followed_call", sa.Boolean),
    sa.Column("alternatives", sa.String),
    sa.Column("exit_conditions", sa.String),
    sa.Column("outcome", sa.String),
    sa.Column("outcome_at", sa.String),
    sa.Column("body", sa.String),
    sa.Column("player_ids", sa.JSON(none_as_null=True)),
    sa.Column("import_key", sa.String),
    sa.UniqueConstraint("season", "import_key", name="uq_decisions_import"),
)
sa.Index("ix_decisions_week_kind", decisions_table.c.season, decisions_table.c.week, decisions_table.c.kind)

# The API's page payloads (api.Cache), written through on every install and loaded expired at boot, so the first open
# of a page after a restart is served at once while it rebuilds behind. A dropped page's row is deleted with it.
page_cache = sa.Table(
    "page_cache", metadata, _season(),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("payload", sa.JSON, nullable=False),
    sa.Column("built_at", sa.Float, nullable=False),     # epoch seconds
    sa.PrimaryKeyConstraint("season", "key"),
)

# ffl/sources' scrapes (sources.Cached), written through on every good fetch and loaded on a source's first read,
# so a restart serves what is still inside its TTL and keeps the rest as last good. `key` is the cache key as
# JSON (a week, "ros"); `data` is [[name, pos, points], ...].
source_cache = sa.Table(
    "source_cache", metadata, _season(),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("key", sa.String, nullable=False),
    sa.Column("data", sa.JSON, nullable=False),
    sa.Column("fetched_at", sa.Float, nullable=False),   # epoch seconds
    sa.Column("ttl", sa.Float, nullable=False),
    sa.PrimaryKeyConstraint("season", "name", "key"),
)


def url() -> str:
    return os.environ.get("FFL_DB_URL") or f"sqlite:///{DATA / 'ffl.db'}"


_ENGINE: sa.Engine | None = None
_ENGINE_LOCK = threading.Lock()


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
            for p in ("busy_timeout=5000", "journal_mode=WAL", "foreign_keys=ON", "synchronous=NORMAL"):
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
        with _ENGINE_LOCK:  # concurrent API threads at boot must not build (and leak) two engines
            if _ENGINE is None:
                _ENGINE = _build_checked()
    return _ENGINE


def _build_checked() -> sa.Engine:
    u = url()
    # connecting would create an empty file that looks like a DB; a missing one is a missing migration
    if u.startswith("sqlite:///") and not os.path.exists(u.removeprefix("sqlite:///")):
        raise SchemaError(f"{u} does not exist: run: uv run alembic upgrade head")
    eng = _make_engine()
    from alembic.runtime.migration import MigrationContext
    with eng.connect() as c:
        current = MigrationContext.configure(c).get_current_revision()
    if current != _head():
        eng.dispose()
        raise SchemaError(f"{u} is at schema {current!r}, code wants {_head()!r}: run: uv run alembic upgrade head")
    return eng


def reset() -> None:
    global _ENGINE
    with _ENGINE_LOCK:
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


def add_transactions(rows: list[dict], season: int = SEASON) -> list[dict]:
    """Insert the rows not already logged (key: season, id). Returns the rows that were new, as
    transactions() shapes them: found by RETURNING, since rowcount is unreliable for a multi-row
    ON CONFLICT DO NOTHING. notify queues a notice for each."""
    if not rows:
        return []
    vals = [{"season": season, "id": r["id"], "date": r["date"], "type": r["type"], "team": r["team"],
             "player": r["player"], "player_id": _tx_player_id(r["id"]), "action": r["action"],
             "bid": int(r.get("bid") or 0), "reasoning": r.get("reasoning") or ""} for r in rows]
    t = transactions_table
    with _write() as c:
        new = c.execute(_upsert(t, vals, ["season", "id"], None).returning(*[t.c[k] for k in _TX_COLS])).all()
    return [dict(r._mapping) for r in new]


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


def put_projection_rows(week: int, kind: str, rows: list[dict], season: int = SEASON) -> int:
    """Upsert each source's points for each player (rows: player_id, source, points). Returns rows written."""
    if not rows:
        return 0
    at = _now()
    vals = [{"season": season, "week": week, "kind": kind, "player_id": r["player_id"], "source": r["source"],
             "points": float(r["points"]), "taken_at": at} for r in rows]
    with _write() as c:
        c.execute(_upsert(projection_rows_table, vals, ["season", "week", "kind", "player_id", "source"],
                          ["points", "taken_at"]))
    return len(vals)


def projection_rows(kind: str, season: int = SEASON, week: int | None = None) -> list[dict]:
    t = projection_rows_table
    q = sa.select(t.c.week, t.c.player_id, t.c.source, t.c.points, t.c.taken_at).where(
        t.c.season == season, t.c.kind == kind).order_by(t.c.week, t.c.player_id, t.c.source)
    if week is not None:
        q = q.where(t.c.week == week)
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def put_source_weights(fit: dict, season: int = SEASON, fitted_at: str | None = None) -> str:
    """Store a fitted set of per-source weights, inactive. Returns its fitted_at key."""
    from datetime import datetime
    at = fitted_at or datetime.now().isoformat(timespec="seconds")
    with _write() as c:
        c.execute(source_weights.insert().values(season=season, fitted_at=at, fit=fit, active=False))
    return at


def source_weight_fits(season: int = SEASON) -> list[dict]:
    q = (sa.select(source_weights.c.fitted_at, source_weights.c.fit, source_weights.c.active)
         .where(source_weights.c.season == season).order_by(source_weights.c.fitted_at.desc()))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def activate_source_weights(fitted_at: str, season: int = SEASON) -> bool:
    """Make one fit the only active one. False (and no change) when that fit is absent."""
    t = source_weights
    with _write() as c:
        if c.execute(sa.select(t.c.fitted_at).where(t.c.season == season, t.c.fitted_at == fitted_at)).first() is None:
            return False
        c.execute(t.update().where(t.c.season == season).values(active=False))
        c.execute(t.update().where(t.c.season == season, t.c.fitted_at == fitted_at).values(active=True))
    return True


def active_source_weights(season: int = SEASON) -> dict | None:
    q = sa.select(source_weights.c.fit).where(source_weights.c.season == season, source_weights.c.active.is_(True))
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


def put_sit(week: int, player_id: int, sat: bool, season: int = SEASON) -> None:
    """Sit him for the week (a repeat is a no-op) or stand him back up (delete)."""
    from datetime import datetime
    with _write() as c:
        if sat:
            c.execute(_upsert(sits, [{"season": season, "week": week, "player_id": player_id,
                                      "at": datetime.now().astimezone().isoformat(timespec="seconds")}],
                              ["season", "week", "player_id"], None))
        else:
            c.execute(sa.delete(sits).where(sits.c.season == season, sits.c.week == week,
                                            sits.c.player_id == player_id))


def seat_map(season: int = SEASON) -> dict[int, int]:
    with _read() as c:
        return dict(c.execute(sa.select(seats.c.player_id, seats.c.seat_id).where(seats.c.season == season)).all())


def put_seats(rows: dict[int, int], season: int = SEASON) -> None:
    """Upsert player -> seat in one transaction."""
    if not rows:
        return
    with _write() as c:
        c.execute(_upsert(seats, [{"season": season, "player_id": p, "seat_id": s} for p, s in rows.items()],
                          ["season", "player_id"], ["seat_id"]))


def sat_ids(week: int, season: int = SEASON) -> set[int]:
    q = sa.select(sits.c.player_id).where(sits.c.season == season, sits.c.week == week)
    with _read() as c:
        return set(c.execute(q).scalars())


def _now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def put_subscription(endpoint: str, keys: dict, ua: str = "") -> None:
    """One device's push subscription; the same endpoint again replaces its keys (browsers rotate them)."""
    with _write() as c:
        c.execute(_upsert(push_subscriptions, [{"endpoint": endpoint, "keys": keys, "ua": ua, "created_at": _now()}],
                          ["endpoint"], ["keys", "ua"]))


def drop_subscription(endpoint: str) -> None:
    with _write() as c:
        c.execute(sa.delete(push_subscriptions).where(push_subscriptions.c.endpoint == endpoint))


def subscriptions() -> list[dict]:
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(push_subscriptions))]


def add_notices(rows: list[dict], season: int = SEASON) -> int:
    """Queue the notices not already known (key: season, key). Returns how many were new."""
    if not rows:
        return 0
    vals = [{"season": season, **{k: r[k] for k in ("key", "kind", "title", "body", "url", "created_at")}} for r in rows]
    with _write() as c:
        return len(c.execute(_upsert(notices, vals, ["season", "key"], None).returning(notices.c.key)).all())


_NOTICE_COLS = ("key", "kind", "title", "body", "url", "created_at", "sent_at", "outcome")


def unsent_notices(season: int = SEASON) -> list[dict]:
    t = notices
    q = (sa.select(*[t.c[k] for k in _NOTICE_COLS]).where(t.c.season == season, t.c.sent_at.is_(None))
         .order_by(t.c.created_at, t.c.key))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def mark_notices(keys: list[str], outcome: str, at: str, season: int = SEASON) -> None:
    if not keys:
        return
    with _write() as c:
        c.execute(sa.update(notices).where(notices.c.season == season, notices.c.key.in_(keys))
                  .values(sent_at=at, outcome=outcome))


def recent_notices(limit: int = 20, season: int = SEASON) -> list[dict]:
    t = notices
    q = (sa.select(*[t.c[k] for k in _NOTICE_COLS]).where(t.c.season == season)
         .order_by(t.c.created_at.desc(), t.c.key.desc()).limit(limit))
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(q)]


def get_state(key: str, default=None, season: int = SEASON):
    q = sa.select(notify_state.c.value).where(notify_state.c.season == season, notify_state.c.key == key)
    with _read() as c:
        v = c.execute(q).scalar()
    return default if v is None else v


def put_state(key: str, value, season: int = SEASON) -> None:
    with _write() as c:
        c.execute(_upsert(notify_state, [{"season": season, "key": key, "value": value}], ["season", "key"], ["value"]))


def put_page(key: str, payload, built_at: float, season: int = SEASON) -> None:
    with _write() as c:
        c.execute(_upsert(page_cache, [{"season": season, "key": key, "payload": payload, "built_at": built_at}],
                          ["season", "key"], ["payload", "built_at"]))


def drop_pages(keys: list[str], season: int = SEASON) -> None:
    if not keys:
        return
    with _write() as c:
        c.execute(page_cache.delete().where(page_cache.c.season == season, page_cache.c.key.in_(keys)))


def drop_page_prefixes(prefixes: tuple[str, ...], season: int = SEASON) -> None:
    """Delete every row keyed `prefix` or `prefix:...`."""
    k = page_cache.c.key
    match = sa.or_(*[sa.or_(k == p, k.startswith(p + ":", autoescape=True)) for p in prefixes])
    with _write() as c:
        c.execute(page_cache.delete().where(page_cache.c.season == season, match))


def clear_pages(season: int = SEASON) -> None:
    with _write() as c:
        c.execute(page_cache.delete().where(page_cache.c.season == season))


def pages(season: int = SEASON) -> list[dict]:
    """Every stored page: {key, payload, built_at}."""
    t = page_cache
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(sa.select(t.c.key, t.c.payload, t.c.built_at).where(t.c.season == season))]


def put_source(name: str, key: str, data: list, fetched_at: float, ttl: float, season: int = SEASON) -> None:
    with _write() as c:
        c.execute(_upsert(source_cache, [{"season": season, "name": name, "key": key, "data": data,
                                          "fetched_at": fetched_at, "ttl": ttl}],
                          ["season", "name", "key"], ["data", "fetched_at", "ttl"]))


def source_rows(name: str, season: int = SEASON) -> list[dict]:
    """One source's stored scrapes: {key, data, fetched_at, ttl}."""
    t = source_cache
    with _read() as c:
        return [dict(r._mapping) for r in c.execute(
            sa.select(t.c.key, t.c.data, t.c.fetched_at, t.c.ttl).where(t.c.season == season, t.c.name == name))]


def _decision_out(r) -> dict:
    # season is implied by the read and import_key is the import's own dedupe key: neither is part of a decision
    return {k: v for k, v in r._mapping.items() if k not in ("season", "import_key")}


def add_decision(row: dict, season: int = SEASON) -> int:
    with _write() as c:
        return c.execute(sa.insert(decisions_table).values({**row, "season": season}).returning(decisions_table.c.id)).scalar()


def put_week_claims(week: int, row: dict, season: int = SEASON) -> dict:
    """The app claims row of the week's plan generation (facts.gen, the plans the waiver run retired before it): a
    resubmit replaces what was sent, keeping the rationale and outcome given since. A plan started after Wednesday's
    run is the same ESPN week but a new decision, so it gets its own row and never inherits the run's reason."""
    t = decisions_table
    keep = {"rationale", "rationale_note", "outcome", "outcome_at"}
    gen = (row.get("facts") or {}).get("gen", 0)
    with _write() as c:
        rows = c.execute(sa.select(t.c.id, t.c.rationale, t.c.facts).where(
            t.c.season == season, t.c.week == week, t.c.kind == "claims", t.c.source == "app")).all()
        old = next((r for r in rows if (r.facts or {}).get("gen", 0) == gen), None)
        if old is None:
            i = c.execute(sa.insert(t).values({**row, "season": season, "week": week}).returning(t.c.id)).scalar()
            return {"id": i, "needs_rationale": row.get("rationale") is None}
        c.execute(sa.update(t).where(t.c.id == old.id).values({k: v for k, v in row.items() if k not in keep}))
        return {"id": old.id, "needs_rationale": old.rationale is None}


def update_decision(id: int, fields: dict, season: int = SEASON) -> dict | None:
    t = decisions_table
    with _write() as c:
        if not c.execute(sa.update(t).where(t.c.season == season, t.c.id == id).values(fields)).rowcount:
            return None
        return _decision_out(c.execute(sa.select(t).where(t.c.id == id)).one())


def decision(id: int, season: int = SEASON) -> dict | None:
    t = decisions_table
    with _read() as c:
        r = c.execute(sa.select(t).where(t.c.season == season, t.c.id == id)).first()
    return _decision_out(r) if r else None


def decisions(season: int = SEASON) -> list[dict]:
    t = decisions_table
    with _read() as c:
        return [_decision_out(r) for r in c.execute(sa.select(t).where(t.c.season == season).order_by(t.c.at.desc(), t.c.id.desc()))]


_KEYS = {"transactions": ["season", "id"], "draft_picks": ["season", "overall"], "results": ["season", "week"],
         "plays": ["season", "week", "player_id", "at", "total"],
         "outlooks": ["season", "week", "kind", "player_id"], "sigma_fits": ["season", "fitted_at"],
         "claim_plans": ["season", "week"], "decisions": ["season", "import_key"]}


def insert_new(table_name: str, rows: list[dict]) -> int:
    """Insert the rows not already present (DO NOTHING on the table's key); returns how many were new.
    Rows carry their own season. For the file import, which must never overwrite."""
    if not rows:
        return 0
    t = metadata.tables[table_name]
    pk = list(t.primary_key.columns)[0]
    with _write() as c:  # batches: one multi-row VALUES would pass SQLite's bind-variable limit on a big table
        return sum(len(c.execute(_upsert(t, rows[i:i + 500], _KEYS[table_name], None).returning(pk)).all())
                   for i in range(0, len(rows), 500))


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
