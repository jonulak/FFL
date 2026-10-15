"""page cache

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('page_cache',
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('built_at', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('season', 'key')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('page_cache')
