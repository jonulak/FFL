"""pairing the browser extension to an account: pending pairings and the paired extensions (ffl-soc.6.3.3)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-28 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0032'
down_revision: Union[str, Sequence[str], None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "espn_pairings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("code_hash", sa.String, nullable=False, unique=True),   # sha256 of the shown code; never the code
        sa.Column("poll_hash", sa.String, nullable=False, unique=True),   # sha256 of the extension's poll secret
        sa.Column("label", sa.String, nullable=False),                    # "Safari on iPhone", off the start's UA
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE")),  # NULL until confirmed
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("expires_at", sa.String, nullable=False),
        sa.Column("confirmed_at", sa.String),
    )
    op.create_table(
        "extension_devices",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String, nullable=False, unique=True),  # sha256 of the device token; never the token
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("last_used_at", sa.String),
    )
    op.create_index("ix_extension_devices_user", "extension_devices", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_extension_devices_user", table_name="extension_devices")
    op.drop_table("extension_devices")
    op.drop_table("espn_pairings")
