"""source cache

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('source_cache',
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('fetched_at', sa.Float(), nullable=False),
    sa.Column('ttl', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('season', 'name', 'key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('source_cache')
