"""league on league data: the league key leads every league-data table's natural key; existing rows are home's

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0021'
down_revision: Union[str, Sequence[str], None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, PK (None: its own `id` column), named uniques, named indexes, FK (cols, parent)), the keys as 0021 has
# them; 0020's are the same less "league". calibration_rows' FK names its parent's new key, so the pair is rebuilt
# together (`_rebuild_calibration`).
_TABLES = [
    ("transactions", ("league", "season", "id"), {}, {"ix_transactions_player": ("league", "season", "player_id")}, None),
    ("results", ("league", "season", "week"), {}, {}, None),
    ("plays", None, {"uq_plays_event": ("league", "season", "week", "player_id", "at", "total")},
     {"ix_plays_week_team": ("league", "season", "week", "team_id")}, None),
    ("box_scores", ("league", "season", "week"), {}, {}, None),
    ("gap_games", ("league", "season", "week", "player_id"), {}, {}, None),
    ("box_score_corrections", None, {}, {}, None),
    ("outlooks", ("league", "season", "week", "kind", "player_id"), {}, {}, None),
    ("calibration_snapshots", ("league", "season", "week"), {}, {}, None),
    ("calibration_rows", ("league", "season", "week", "player_id"), {}, {},
     (("league", "season", "week"), "calibration_snapshots")),
    ("projection_rows", ("league", "season", "week", "kind", "player_id", "source"), {}, {}, None),
    ("claim_plans", ("league", "season", "week"), {}, {}, None),
    ("sits", ("league", "season", "week", "player_id"), {}, {}, None),
    ("seats", ("league", "season", "player_id"), {}, {}, None),
    ("watchlist", ("league", "season", "player_id"), {}, {}, None),
    ("watch_signals", ("league", "season", "key"), {}, {"ix_watch_signals_player": ("league", "season", "player_id")},
     None),
    ("notices", ("league", "season", "key"), {}, {"ix_notices_unsent": ("league", "season", "sent_at")}, None),
    ("decisions", None, {"uq_decisions_import": ("league", "season", "import_key")},
     {"ix_decisions_week_kind": ("league", "season", "week", "kind")}, None),
]
_HOME = "(SELECT key FROM leagues WHERE home)"


def _keep(cols, league: bool) -> tuple:
    return tuple(cols) if league else tuple(c for c in cols if c != "league")


def _create_copy(bind, spec, league: bool, where: str, params: dict, parent_new: bool = False) -> None:
    """Create `_<name>_new` with the key `spec` gives (with or without league) and copy every row the WHERE keeps
    in rowid order: the transaction log is read in insert order, and the id tables keep their ids."""
    name, pk, uniques, _indexes, fk = spec
    reflected = sa.inspect(bind).get_columns(name)     # a fresh inspector: the DDL before this changed the schema
    old_pk = set(sa.inspect(bind).get_pk_constraint(name)["constrained_columns"])
    copied = [c["name"] for c in reflected if c["name"] != "league"]
    cols = [sa.Column("league", sa.String(), nullable=False)] if league else []
    for c in reflected:
        if c["name"] == "league":
            continue
        default = c.get("default")
        cols.append(sa.Column(c["name"], c["type"], nullable=c["nullable"],
                              server_default=sa.text(default) if default is not None else None,
                              primary_key=pk is None and c["name"] in old_pk))
    extra = []
    if pk is not None:
        extra.append(sa.PrimaryKeyConstraint(*_keep(pk, league)))
    extra += [sa.UniqueConstraint(*_keep(u, league), name=n) for n, u in uniques.items()]
    if fk is not None:
        fcols, parent = _keep(fk[0], league), fk[1]
        target = f"_{parent}_new" if parent_new else parent
        extra.append(sa.ForeignKeyConstraint(list(fcols), [f"{target}.{c}" for c in fcols]))
    op.create_table(f"_{name}_new", *cols, *extra)
    q = bind.dialect.identifier_preparer.quote
    names = ", ".join(q(c) for c in copied)
    into = f"league, {names}" if league else names
    select = f":home, {names}" if league else names
    bind.execute(sa.text(f"INSERT INTO _{name}_new ({into}) SELECT {select} FROM {name} {where} ORDER BY rowid"), params)


def _swap(spec, league: bool) -> None:
    """Drop the old table, put the new one in its place and create its named indexes (index names are
    database-wide in SQLite, so only once the old table and its indexes are gone)."""
    name, _pk, _uniques, indexes, _fk = spec
    op.drop_table(name)
    op.rename_table(f"_{name}_new", name)
    for n, cols in indexes.items():
        op.create_index(n, name, list(_keep(cols, league)))


def _rebuild_all(league: bool, where: str, params: dict) -> None:
    bind = op.get_bind()
    by_name = {s[0]: s for s in _TABLES}
    for spec in _TABLES:
        if spec[0] in ("calibration_snapshots", "calibration_rows"):
            continue
        _create_copy(bind, spec, league, where, params)
        _swap(spec, league)
    # the pair: both new tables (the child's FK on the new parent), both copied, the old child dropped before the old
    # parent, then the renames (SQLite rewrites the child's REFERENCES to the parent's final name). Safe whether or not
    # the connection enforces foreign keys.
    parent, child = by_name["calibration_snapshots"], by_name["calibration_rows"]
    _create_copy(bind, parent, league, where, params)
    _create_copy(bind, child, league, where, params, parent_new=True)
    op.drop_table("calibration_rows")
    op.drop_table("calibration_snapshots")
    op.rename_table("_calibration_snapshots_new", "calibration_snapshots")
    op.rename_table("_calibration_rows_new", "calibration_rows")


def _home_or_refuse(bind, what: str, then: str):
    """The home league's key; None only when the tables are empty (rows and no home league: RuntimeError)."""
    n = sum(bind.execute(sa.text(f"SELECT count(*) FROM {s[0]}")).scalar() for s in _TABLES)
    home = bind.execute(sa.text("SELECT key FROM leagues WHERE home")).scalar()
    if n and home is None:
        raise RuntimeError(f"0021: the league-data tables hold {n} rows and there is no home league {what}; register "
                           f"it first (`uv run python -m ffl.leagues add espn <id> --home`), then {then}")
    return home


def upgrade() -> None:
    """Upgrade schema; every existing row is the home league's (the only league the season side has run on)."""
    home = _home_or_refuse(op.get_bind(), "to file them under", "upgrade")
    _rebuild_all(True, "", {"home": home})


def downgrade() -> None:
    """Downgrade schema; only the home league's rows fit the old keys, so the others are dropped (with no home
    league every row would be: refused)."""
    _home_or_refuse(op.get_bind(), "whose rows to keep", "downgrade")
    _rebuild_all(False, f"WHERE league = {_HOME}", {})
