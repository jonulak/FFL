"""news_stories.embedding: a vector of each written story (pgvector), for GET /news/search

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-01 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0038'
down_revision: Union[str, Sequence[str], None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not bind.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar():
        raise RuntimeError(
            "this Postgres has no pgvector. Docker install: re-run install.sh (it fetches the new docker-compose.yml, "
            "whose db image is pgvector/pgvector:pg16), then `docker compose up -d`. Mac: `brew install pgvector`.")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # No index: an exact scan over a season's stories is milliseconds; add HNSW when that stops being true.
    op.execute("ALTER TABLE news_stories ADD COLUMN embedding vector(384)")


def downgrade() -> None:
    op.drop_column("news_stories", "embedding")  # the extension stays: dropping it needs a superuser and buys nothing
