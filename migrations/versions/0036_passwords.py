"""passwords: users carry a username, password hash, admin flag and setup token; identities goes, email is optional

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-31 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0036'
down_revision: Union[str, Sequence[str], None] = '0035'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("username", sa.String))
        b.add_column(sa.Column("password_hash", sa.String))
        b.add_column(sa.Column("is_admin", sa.Boolean, nullable=False, server_default=sa.false()))
        b.add_column(sa.Column("setup_token_hash", sa.String))
        b.add_column(sa.Column("setup_expires", sa.String))
        b.alter_column("email", existing_type=sa.String, nullable=True)
    op.create_index("uq_users_username_lower", "users", [sa.text("lower(username)")], unique=True)
    op.execute("UPDATE users SET is_admin = true WHERE id = (SELECT min(id) FROM users)")
    op.drop_table("identities")


def downgrade() -> None:
    op.create_table(
        'identities',
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('subject', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('provider', 'subject'),
    )
    op.drop_index("uq_users_username_lower", table_name="users")
    with op.batch_alter_table("users") as b:
        b.alter_column("email", existing_type=sa.String, nullable=False)  # fails while a user has no email
        for col in ("setup_expires", "setup_token_hash", "is_admin", "password_hash", "username"):
            b.drop_column(col)
