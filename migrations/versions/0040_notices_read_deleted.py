"""notices.read_at, notices.deleted_at: the in-app list. What was already delivered counts as read.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-06 12:00:00.000000
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0040'
down_revision: Union[str, Sequence[str], None] = '0039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notices", sa.Column("read_at", sa.String))
    op.add_column("notices", sa.Column("deleted_at", sa.String))
    # Or the first badge is a season's worth. A queued row stays unread: a read notice is not pushed.
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    op.execute(sa.text("UPDATE notices SET read_at = :at WHERE sent_at IS NOT NULL").bindparams(at=at))


def downgrade() -> None:
    op.drop_column("notices", "deleted_at")
    op.drop_column("notices", "read_at")
