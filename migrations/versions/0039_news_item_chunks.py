"""news_item_chunks: each source item's text in pieces, a vector each (pgvector), for GET /news/search

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-02 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = '0039'
down_revision: Union[str, Sequence[str], None] = '0038'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No vector index: an exact scan over a season's chunks (~14k) is milliseconds; add HNSW when that stops being true.
    op.create_table(
        "news_item_chunks",
        sa.Column("season", sa.Integer, nullable=False),
        sa.Column("item_id", sa.Integer, nullable=False),
        sa.Column("pos", sa.Integer, nullable=False),
        sa.Column("hash", sa.String, nullable=False),
        sa.Column("text", sa.String, nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.PrimaryKeyConstraint("season", "item_id", "pos"),
    )


def downgrade() -> None:
    op.drop_table("news_item_chunks")
