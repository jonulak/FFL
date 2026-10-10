"""notify

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09 22:05:28.871441

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('notices',
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('body', sa.String(), nullable=False),
    sa.Column('url', sa.String(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('sent_at', sa.String(), nullable=True),
    sa.Column('outcome', sa.String(), nullable=True),
    sa.PrimaryKeyConstraint('season', 'key')
    )
    op.create_index('ix_notices_unsent', 'notices', ['season', 'sent_at'], unique=False)
    op.create_table('notify_state',
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('season', 'key')
    )
    op.create_table('push_subscriptions',
    sa.Column('endpoint', sa.String(), nullable=False),
    sa.Column('keys', sa.JSON(), nullable=False),
    sa.Column('ua', sa.String(), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('endpoint')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('push_subscriptions')
    op.drop_table('notify_state')
    op.drop_index('ix_notices_unsent', table_name='notices')
    op.drop_table('notices')
