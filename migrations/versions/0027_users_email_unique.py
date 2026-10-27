"""users: one per email whatever its case (ffl-soc.6.2.6)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-27 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0027'
down_revision: Union[str, Sequence[str], None] = '0026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """A unique index on lower(email). Users already split by case are refused by name, never merged here: whose data
    goes where is a person's call."""
    dupes = op.get_bind().execute(sa.text(
        "SELECT lower(email) FROM users GROUP BY lower(email) HAVING count(*) > 1 ORDER BY 1")).scalars().all()
    if dupes:
        raise RuntimeError(f"users share an email in different case, merge them first: {', '.join(dupes)}")
    op.create_index('uq_users_email_lower', 'users', [sa.text('lower(email)')], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_users_email_lower', table_name='users')
