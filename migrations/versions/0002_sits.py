"""sits

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('sits',
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('week', sa.Integer(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=False),
    sa.Column('at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('season', 'week', 'player_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('sits')
