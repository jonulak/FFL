"""leagues

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-24 09:00:00.000000

"""
import os
from datetime import date, datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema; seed the home league from .env, the last time league facts are read from it."""
    leagues = op.create_table('leagues',
    sa.Column('key', sa.String(), nullable=False),
    sa.Column('provider', sa.String(), nullable=False),
    sa.Column('provider_id', sa.String(), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('home', sa.Boolean(), nullable=False),
    sa.Column('lineup_format', sa.String(), nullable=True),
    sa.Column('strategy', sa.String(), nullable=True),
    sa.Column('added_at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    league_id = (os.environ.get("LEAGUE_ID") or "").strip()
    if league_id and league_id != "0":
        today = date.today()
        season = int(os.environ.get("SEASON") or (today.year if today.month >= 3 else today.year - 1))
        team = int(os.environ.get("MY_TEAM_ID") or 0) or None
        op.bulk_insert(leagues, [{
            "key": f"espn:{league_id}", "provider": "espn", "provider_id": league_id, "season": season,
            "name": "home", "team_id": team, "home": True, "lineup_format": None, "strategy": None,
            "added_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('leagues')
