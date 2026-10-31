"""outlooks shared across leagues: one row per (season, week, player), the newest written

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-31 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0035'
down_revision: Union[str, Sequence[str], None] = '0034'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outlooks_new",
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("week", sa.Integer, nullable=False),
        sa.Column("player_id", sa.Integer, nullable=False),
        sa.Column("entry", sa.JSON, nullable=False),
        sa.Column("updated", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("season", "week", "player_id"),
    )
    op.execute("INSERT INTO outlooks_new (season, week, player_id, entry, updated) "
               "SELECT DISTINCT ON (season, week, player_id) season, week, player_id, entry, updated FROM outlooks "
               "ORDER BY season, week, player_id, updated DESC")
    op.drop_table("outlooks")
    op.rename_table("outlooks_new", "outlooks")
    op.execute("ALTER INDEX outlooks_new_pkey RENAME TO outlooks_pkey")


def downgrade() -> None:
    # league and kind cannot be recovered: the rows go, the outlook run rewrites them
    op.drop_table("outlooks")
    op.create_table(
        "outlooks",
        sa.Column("league", sa.String, nullable=False),
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("week", sa.Integer, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("player_id", sa.Integer, nullable=False),
        sa.Column("entry", sa.JSON, nullable=False),
        sa.Column("updated", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("league", "season", "week", "kind", "player_id"),
    )
