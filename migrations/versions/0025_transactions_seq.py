"""transactions seq: the log's insert order as a column (Postgres has no rowid)

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-26 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0025'
down_revision: Union[str, Sequence[str], None] = '0024'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema; rows already here are numbered in no particular order (a copy from the SQLite file sets
    seq itself, in its rowid order: the identity is BY DEFAULT, so an explicit value is taken)."""
    op.add_column('transactions', sa.Column('seq', sa.BigInteger(), sa.Identity(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('transactions', 'seq')
