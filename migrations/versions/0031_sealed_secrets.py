"""secrets at rest: the ESPN credentials table (sealed cookies per user) and push_subscriptions.keys sealed in place
(ffl-soc.3)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from ffl import vault


# revision identifiers, used by Alembic.
revision: str = '0031'
down_revision: Union[str, Sequence[str], None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _aad(endpoint: str) -> str:
    return f"push_subscriptions:{endpoint}:keys"   # ffl/db.py's, which reads them


def upgrade() -> None:
    """Every push row's keys sealed as the JSON text they were, with FFL_SECRET_KEYS (alembic runs with /etc/ffl/env on
    the box). Rows and no ring: vault.VaultError, and the upgrade's one transaction leaves 0030."""
    op.create_table(
        "espn_credentials",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("secret", sa.String, nullable=False),        # vault token of {"espn_s2", "swid"}
        sa.Column("updated_at", sa.String, nullable=False),
        sa.Column("last_ok", sa.String),                       # the last ESPN read these cookies passed
        sa.Column("failing_since", sa.String),                 # ESPN refused them (401/403) since; NULL: not failing
        sa.Column("failing_reason", sa.String),
    )
    bind = op.get_bind()
    op.alter_column("push_subscriptions", "keys", type_=sa.String, existing_nullable=False,
                    postgresql_using="keys::text")
    for endpoint, keys in bind.execute(sa.text("SELECT endpoint, keys FROM push_subscriptions")).all():
        bind.execute(sa.text("UPDATE push_subscriptions SET keys = :k WHERE endpoint = :e"),
                     {"k": vault.seal(keys, aad=_aad(endpoint)), "e": endpoint})


def downgrade() -> None:
    bind = op.get_bind()
    for endpoint, keys in bind.execute(sa.text("SELECT endpoint, keys FROM push_subscriptions")).all():
        bind.execute(sa.text("UPDATE push_subscriptions SET keys = :k WHERE endpoint = :e"),
                     {"k": vault.open_(keys, aad=_aad(endpoint)), "e": endpoint})
    op.alter_column("push_subscriptions", "keys", type_=sa.JSON, existing_nullable=False,
                    postgresql_using="keys::json")
    op.drop_table("espn_credentials")
