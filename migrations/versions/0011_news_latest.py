"""news: a story's news time (latest_at) and its pending headline refreshes (reword)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('news_stories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('latest_at', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('reword', sa.Integer(), server_default=sa.text('0'), nullable=False))
    # the newest live source's time; a story with none (retracted) keeps its last update
    op.execute("""
        UPDATE news_stories SET latest_at = coalesce(
            (SELECT max(i.published_at) FROM news_items i
             WHERE i.season = news_stories.season AND i.story_id = news_stories.id AND i.withdrawn_at IS NULL),
            updated_at)
    """)
    with op.batch_alter_table('news_stories', schema=None) as batch_op:
        batch_op.alter_column('latest_at', existing_type=sa.String(), nullable=False)
        batch_op.create_index('ix_news_stories_latest', ['season', 'latest_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('news_stories', schema=None) as batch_op:
        batch_op.drop_index('ix_news_stories_latest')
        batch_op.drop_column('reword')
        batch_op.drop_column('latest_at')
