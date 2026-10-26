"""player views shared: the Players table's views (player_presets) are every league's; home's become the shared ones

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-25 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0024'
down_revision: Union[str, Sequence[str], None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HOME = "(SELECT key FROM leagues WHERE home)"


def upgrade() -> None:
    """Home's views move to the global state; any other league's are dropped (one set of views for every league)."""
    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO notify_state (season, key, value) "
                         f"SELECT season, key, value FROM league_state WHERE key = 'player_presets' AND league = {_HOME} "
                         "ON CONFLICT (season, key) DO UPDATE SET value = excluded.value"))
    bind.execute(sa.text("DELETE FROM league_state WHERE key = 'player_presets'"))


def downgrade() -> None:
    """The shared views go back to the home league (none without one)."""
    bind = op.get_bind()
    bind.execute(sa.text("INSERT INTO league_state (league, season, key, value) "
                         f"SELECT {_HOME}, season, key, value FROM notify_state "
                         f"WHERE key = 'player_presets' AND {_HOME} IS NOT NULL "
                         "ON CONFLICT (league, season, key) DO UPDATE SET value = excluded.value"))
    bind.execute(sa.text("DELETE FROM notify_state WHERE key = 'player_presets'"))
