"""called back per league: a called-back row is one league's starter priced on its table; existing rows are home's

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-25 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0023'
down_revision: Union[str, Sequence[str], None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HOME = "(SELECT key FROM leagues WHERE home)"
_COLS = "season, play_id, player_id, week, body"


def _rename_parts(bind, name: str) -> None:
    """The renamed copy keeps the names Postgres gave `_<name>_new`'s constraints (its key; on PG 18 its NOT NULLs
    too): give them the names a fresh create_table(name) has, so the schema matches one built at head."""
    prefix = f"_{name}_new_"
    for (con,) in bind.execute(sa.text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
                               {"t": name}).all():
        if con.startswith(prefix):
            bind.execute(sa.text(f'ALTER TABLE {name} RENAME CONSTRAINT "{con}" TO "{name}_{con[len(prefix):]}"'))


def _rebuild(league: bool, where: str, params: dict) -> None:
    """Rebuild called_back with or without the league key, copying the rows the WHERE keeps."""
    bind = op.get_bind()
    cols = [sa.Column("league", sa.String(), nullable=False)] if league else []
    cols += [sa.Column("season", sa.Integer(), nullable=False), sa.Column("play_id", sa.String(), nullable=False),
             sa.Column("player_id", sa.Integer(), nullable=False), sa.Column("week", sa.Integer(), nullable=False),
             sa.Column("body", sa.JSON(), nullable=False)]
    pk = ("league", "season", "play_id", "player_id") if league else ("season", "play_id", "player_id")
    op.create_table("_called_back_new", *cols, sa.PrimaryKeyConstraint(*pk))
    into, select = (f"league, {_COLS}", f":home, {_COLS}") if league else (_COLS, _COLS)
    bind.execute(sa.text(f"INSERT INTO _called_back_new ({into}) SELECT {select} FROM called_back {where}"), params)
    op.drop_table("called_back")                         # its index with it: index names are schema-wide
    op.rename_table("_called_back_new", "called_back")
    _rename_parts(bind, "called_back")
    op.create_index("ix_called_back_week", "called_back", ["league", "season", "week"] if league else ["season", "week"])


def _home_or_refuse(what: str, then: str):
    bind = op.get_bind()
    n = bind.execute(sa.text("SELECT count(*) FROM called_back")).scalar()
    home = bind.execute(sa.text("SELECT key FROM leagues WHERE home")).scalar()
    if n and home is None:
        raise RuntimeError(f"0023: called_back holds {n} rows and there is no home league {what}; register it first "
                           f"(`uv run python -m ffl.leagues add espn <id> --home`), then {then}")
    return home


def upgrade() -> None:
    """Upgrade schema; every existing row is the home league's (the only league the feed has scored)."""
    _rebuild(True, "", {"home": _home_or_refuse("to file them under", "upgrade")})


def downgrade() -> None:
    """Downgrade schema; only the home league's rows fit the old key, so the others are dropped."""
    _home_or_refuse("whose rows to keep", "downgrade")
    _rebuild(False, f"WHERE league = {_HOME}", {})
