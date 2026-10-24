"""draft inputs: the ESPN pool per position scoring format, Sleeper's raw season projections, FFC ADP feeds

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24 23:00:00.000000

"""
import json
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0017'
down_revision: Union[str, Sequence[str], None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _mtime(path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def upgrade() -> None:
    """Upgrade schema; import <DATA>/espn_players.csv as the home league's pool, under its six position formats (its
    ownership columns, on_team_id and status, left out: a pool row is shared by format), and
    adp_2qb.csv / adp_1qb.csv as its feeds, the last time they are read. sleeper_players.csv is already rescored, so
    it is not imported: Sleeper's raw season projections are fetched on first use."""
    espn_pools = op.create_table('espn_pools',
        sa.Column('scoring_format', sa.String(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('rows', sa.JSON(), nullable=False),
        sa.Column('fetched_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('scoring_format', 'season'))
    op.create_table('sleeper_season',
        sa.Column('season', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('raw', sa.JSON(), nullable=False),
        sa.Column('fetched_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('season'))
    adp_feeds = op.create_table('adp_feeds',
        sa.Column('fmt', sa.String(), nullable=False),
        sa.Column('teams', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('rows', sa.JSON(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=False),
        sa.Column('fetched_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('fmt', 'teams', 'year'))

    import pandas as pd
    from ffl import adp, config, players
    from ffl.settings import key_scoring
    files = {n: config.DATA / n for n in ("espn_players.csv", "adp_2qb.csv", "adp_1qb.csv")}
    if not any(p.exists() for p in files.values()):
        return
    bind = op.get_bind()
    home = bind.execute(sa.text("SELECT key, season, lineup_format FROM leagues WHERE home")).first()
    keys = {r.pos: r.scoring_format for r in bind.execute(
        sa.text("SELECT pos, scoring_format FROM league_scoring WHERE league = :k"), {"k": home.key})} if home else {}
    if home is None or set(keys) != set(players.POSITIONS):
        print("0017: no home league with stored settings; draft inputs not imported "
              "(run `uv run python -m ffl.players` and `uv run python -m ffl.adp`)")
        return

    path = files["espn_players.csv"]
    if path.exists():
        df = pd.read_csv(path)
        op.bulk_insert(espn_pools, [{"scoring_format": keys[pos], "season": home.season,
                                     "rows": adp.records(df[df.pos == pos].drop(columns=list(players.LEAGUE_COLUMNS),
                                                                                errors="ignore")),
                                     "fetched_at": _mtime(path)}
                                    for pos in players.POSITIONS])

    lineup = bind.execute(sa.text("SELECT doc FROM lineup_formats WHERE key = :k"), {"k": home.lineup_format}).scalar()
    doc = bind.execute(sa.text("SELECT doc FROM league_settings WHERE league = :k AND season = :s"),
                       {"k": home.key, "s": home.season}).scalar()
    if lineup is None or doc is None:
        print("0017: the home league's lineup or settings are missing; ADP feeds not imported")
        return
    teams = _json(lineup)["team_count"]
    one_fmt = adp.one_qb_format(key_scoring(_json(doc)["scoring"])["ppr"])
    feeds = []
    for fmt, name in (("2qb", "adp_2qb.csv"), (one_fmt, "adp_1qb.csv")):
        if files[name].exists():
            feeds.append({"fmt": fmt, "teams": teams, "year": home.season, "meta": {},
                          "rows": adp.records(pd.read_csv(files[name])), "fetched_at": _mtime(files[name])})
    if feeds:
        op.bulk_insert(adp_feeds, feeds)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('adp_feeds')
    op.drop_table('sleeper_season')
    op.drop_table('espn_pools')
