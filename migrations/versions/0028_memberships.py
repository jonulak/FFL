"""memberships: a user's place in a league; page_cache per user (ffl-soc.6.2.2)

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-27 18:00:00.000000

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0028'
down_revision: Union[str, Sequence[str], None] = '0027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """memberships, and every league filed under the lowest user id with the leagues row's team, strategy and home
    (as the default); the league's name stays the league's (a member's own name starts NULL). No user: nothing filed,
    and nothing is ever filed at sign-in. page_cache gains user_id in its key, its rows the same user's (0: none)."""
    op.create_table(
        'memberships',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=True),
        sa.Column('is_default', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('strategy', sa.String(), nullable=True),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['league'], ['leagues.key'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'league'),
    )
    op.create_index('uq_memberships_default', 'memberships', ['user_id'], unique=True,
                    postgresql_where=sa.text('is_default'))
    bind = op.get_bind()
    owner = bind.execute(sa.text("SELECT min(id) FROM users")).scalar()
    if owner is not None:
        bind.execute(sa.text(
            "INSERT INTO memberships (user_id, league, team_id, is_default, name, strategy, created_at) "
            "SELECT :u, key, team_id, home, NULL, strategy, :now FROM leagues"),
            {"u": owner, "now": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    op.add_column('page_cache', sa.Column('user_id', sa.Integer(), server_default='0', nullable=False))
    if owner is not None:
        bind.execute(sa.text("UPDATE page_cache SET user_id = :u"), {"u": owner})
    pk = bind.execute(sa.text("SELECT conname FROM pg_constraint WHERE conrelid = 'page_cache'::regclass "
                              "AND contype = 'p'")).scalar()
    op.drop_constraint(pk, 'page_cache', type_='primary')
    op.create_primary_key('page_cache_pkey', 'page_cache', ['user_id', 'league', 'season', 'key'])


def downgrade() -> None:
    """Downgrade schema: the owner's pages kept, every other member's dropped."""
    op.execute("DELETE FROM page_cache WHERE user_id <> (SELECT coalesce(min(id), 0) FROM users) AND user_id <> 0")
    op.drop_constraint('page_cache_pkey', 'page_cache', type_='primary')
    op.create_primary_key('page_cache_pkey', 'page_cache', ['league', 'season', 'key'])
    op.drop_column('page_cache', 'user_id')
    op.drop_index('uq_memberships_default', table_name='memberships', postgresql_where=sa.text('is_default'))
    op.drop_table('memberships')
