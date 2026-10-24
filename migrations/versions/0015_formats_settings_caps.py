"""scoring and lineup formats, league settings and caps

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24 18:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# my caps in the home league (STRATEGY.md's roster rules), seeded with its import
HOME_MY_CAP = {"QB": 3, "RB": 7, "WR": 7, "TE": 2, "K": 1, "D/ST": 1}


def upgrade() -> None:
    """Upgrade schema; import <DATA>/settings.json as the home league's settings, the last time it is read."""
    tables = {
        "scoring_formats": op.create_table('scoring_formats',
            sa.Column('key', sa.String(), nullable=False),
            sa.Column('pos', sa.String(), nullable=False),
            sa.Column('rescore_key', sa.String(), nullable=False),
            sa.Column('table', sa.JSON(), nullable=False),
            sa.Column('added_at', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('key')),
        "league_scoring": op.create_table('league_scoring',
            sa.Column('league', sa.String(), nullable=False),
            sa.Column('pos', sa.String(), nullable=False),
            sa.Column('scoring_format', sa.String(), nullable=False),
            sa.Column('unmapped', sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint('league', 'pos')),
        "lineup_formats": op.create_table('lineup_formats',
            sa.Column('key', sa.String(), nullable=False),
            sa.Column('doc', sa.JSON(), nullable=False),
            sa.Column('added_at', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('key')),
        "league_settings": op.create_table('league_settings',
            sa.Column('league', sa.String(), nullable=False),
            sa.Column('season', sa.Integer(), nullable=False),
            sa.Column('doc', sa.JSON(), nullable=False),
            sa.Column('fetched_at', sa.String(), nullable=False),
            sa.PrimaryKeyConstraint('league', 'season')),
        "league_caps": op.create_table('league_caps',
            sa.Column('league', sa.String(), nullable=False),
            sa.Column('pos', sa.String(), nullable=False),
            sa.Column('provider_limit', sa.Integer(), nullable=True),
            sa.Column('my_cap', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('league', 'pos')),
    }
    from ffl import config, formats
    path = config.DATA / "settings.json"
    if not path.exists():
        return
    bind = op.get_bind()
    home = bind.execute(sa.text("SELECT key, provider_id, season FROM leagues WHERE home")).first()
    doc = json.loads(path.read_text())
    if home is None or str(doc.get("league_id")) != home.provider_id:
        print(f"0015: {path} is not the home league's settings; not imported (run `uv run python -m ffl.settings`)")
        return
    rows = formats.rows_for(home.key, home.season, doc)
    for c in rows["league_caps"]:
        c["my_cap"] = HOME_MY_CAP.get(c["pos"])
    for name, table in tables.items():
        op.bulk_insert(table, rows[name])
    bind.execute(sa.text("UPDATE leagues SET lineup_format = :lf, name = COALESCE(:name, name) WHERE key = :key"),
                 {"lf": rows["lineup_key"], "name": rows["name"], "key": home.key})


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE leagues SET lineup_format = NULL")
    for t in ('league_caps', 'league_settings', 'lineup_formats', 'league_scoring', 'scoring_formats'):
        op.drop_table(t)
