"""draft per league: draft_picks keyed by league, and the live draft's feed and state in the DB

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-25 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0018'
down_revision: Union[str, Sequence[str], None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = ("season", "overall", "round", "pick", "team_id", "team", "player_id", "player", "pos", "nfl", "adp_2qb",
         "board_rank_at_pick", "proj", "ts")


def _rename_parts(bind, name: str) -> None:
    """The renamed copy keeps the names Postgres gave `_<name>_new`'s constraints (its key; on PG 18 its NOT NULLs
    too): give them the names a fresh create_table(name) has, so the schema matches one built at head."""
    prefix = f"_{name}_new_"
    for (con,) in bind.execute(sa.text("SELECT conname FROM pg_constraint WHERE conrelid = CAST(:t AS regclass)"),
                               {"t": name}).all():
        if con.startswith(prefix):
            bind.execute(sa.text(f'ALTER TABLE {name} RENAME CONSTRAINT "{con}" TO "{name}_{con[len(prefix):]}"'))


def _pick_columns() -> list[sa.Column]:
    return [sa.Column('season', sa.Integer(), nullable=False),
            sa.Column('overall', sa.Integer(), nullable=False),
            sa.Column('round', sa.Integer(), nullable=False),
            sa.Column('pick', sa.Integer(), nullable=False),
            sa.Column('team_id', sa.Integer(), nullable=False),
            sa.Column('team', sa.String(), nullable=False),
            sa.Column('player_id', sa.Integer(), nullable=False),
            sa.Column('player', sa.String(), nullable=False),
            sa.Column('pos', sa.String(), nullable=True),
            sa.Column('nfl', sa.String(), nullable=True),
            sa.Column('adp_2qb', sa.Float(), nullable=True),
            sa.Column('board_rank_at_pick', sa.Float(), nullable=True),
            sa.Column('proj', sa.Float(), nullable=True),
            sa.Column('ts', sa.String(), nullable=True)]


def _recreate(league_first: bool, select: str, params: dict) -> None:
    """Rebuild draft_picks (the batch-mode recreate, done by hand: SQLite's PK is unnamed, so it cannot be
    altered in place) with or without the league column, copying every row through `select`."""
    cols = _pick_columns()
    pk = ('league', 'season', 'overall') if league_first else ('season', 'overall')
    if league_first:
        cols = [sa.Column('league', sa.String(), nullable=False)] + cols
    op.create_table('_draft_picks_new', *cols, sa.PrimaryKeyConstraint(*pk))
    names = (["league"] if league_first else []) + list(_COLS)
    op.get_bind().execute(sa.text(f"INSERT INTO _draft_picks_new ({', '.join(names)}) {select}"), params)
    op.drop_table('draft_picks')
    op.rename_table('_draft_picks_new', 'draft_picks')
    _rename_parts(op.get_bind(), 'draft_picks')


def upgrade() -> None:
    """Upgrade schema; every existing pick is the home league's (its 2026 draft, the only one before this)."""
    bind = op.get_bind()
    n = bind.execute(sa.text("SELECT count(*) FROM draft_picks")).scalar()
    home = bind.execute(sa.text("SELECT key FROM leagues WHERE home")).scalar()
    if n and home is None:
        raise RuntimeError(f"0018: draft_picks holds {n} picks and there is no home league to file them under; "
                           "register it first (`uv run python -m ffl.leagues add espn <id> --home`), then upgrade")
    _recreate(True, f"SELECT :home, {', '.join(_COLS)} FROM draft_picks", {"home": home})
    op.create_table('draft_feeds',
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('picks', sa.JSON(), nullable=False),
        sa.Column('offset', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('league', 'season'))
    op.create_table('draft_states',
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('doc', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('league', 'season'))


def downgrade() -> None:
    """Downgrade schema; only the home league's picks fit the old key, so the others are dropped."""
    op.drop_table('draft_states')
    op.drop_table('draft_feeds')
    _recreate(False, f"SELECT {', '.join(_COLS)} FROM draft_picks "
                     "WHERE league = (SELECT key FROM leagues WHERE home)", {})
