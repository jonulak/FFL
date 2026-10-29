"""several ESPN accounts per user: espn_credentials keyed by row, each league's membership naming its account (#460)

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-28 23:59:00.000000

Nothing is opened or re-sealed here (no key ring in a migration): an existing row keeps its secret and gets its
swid_hash the first time the app opens it; a membership's espn_account NULL means the user's first account. A row
the app's ring cannot open (a test server's copy of production) is never named, and the user's next connect drops it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0033'
down_revision: Union[str, Sequence[str], None] = '0032'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("espn_credentials_pkey", "espn_credentials", type_="primary")
    op.execute("ALTER TABLE espn_credentials ADD COLUMN id SERIAL PRIMARY KEY")
    op.add_column("espn_credentials", sa.Column("swid_hash", sa.String))   # sha256 of the SWID; NULL until opened
    op.create_unique_constraint("uq_espn_credentials_account", "espn_credentials", ["user_id", "swid_hash"])
    op.create_index("ix_espn_credentials_user", "espn_credentials", ["user_id"])
    op.add_column("memberships", sa.Column("espn_account", sa.String))     # the account's swid_hash; NULL: the first


def downgrade() -> None:
    """Keeps each user's first account (lowest id); any other is deleted."""
    op.drop_column("memberships", "espn_account")
    op.execute("DELETE FROM espn_credentials e USING espn_credentials f "
               "WHERE e.user_id = f.user_id AND e.id > f.id")
    op.drop_index("ix_espn_credentials_user", table_name="espn_credentials")
    op.drop_constraint("uq_espn_credentials_account", "espn_credentials", type_="unique")
    op.drop_column("espn_credentials", "swid_hash")
    op.drop_constraint("espn_credentials_pkey", "espn_credentials", type_="primary")
    op.drop_column("espn_credentials", "id")
    op.create_primary_key("espn_credentials_pkey", "espn_credentials", ["user_id"])
