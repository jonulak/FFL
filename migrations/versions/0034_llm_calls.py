"""llm_calls: one row per `claude -p` call, its kind, model, league, tokens and cost (#64)

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-31 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0034'
down_revision: Union[str, Sequence[str], None] = '0033'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("league", sa.String),
        sa.Column("player_id", sa.Integer),
        sa.Column("input", sa.Integer, nullable=False),
        sa.Column("output", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column("ok", sa.Boolean, nullable=False),
    )
    op.create_index("ix_llm_calls_at", "llm_calls", ["at"])
    op.create_index("ix_llm_calls_kind", "llm_calls", ["kind"])


def downgrade() -> None:
    op.drop_table("llm_calls")
