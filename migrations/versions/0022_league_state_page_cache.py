"""league state and page cache: a league's own state keys leave notify_state for league_state; page_cache is keyed by league

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-25 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0022'
down_revision: Union[str, Sequence[str], None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ffl.db.LEAGUE_STATE_KEYS as 0022 has them (a migration never imports ffl.db).
_KEYS = ("waiver_run", "pending_claims", "watch_state", "player_presets", "pregame_pulls", "decision_prefs",
         "final_plays_done", "news_to_notify")
_IN = ", ".join(f"'{k}'" for k in _KEYS)
_HOME = "(SELECT key FROM leagues WHERE home)"


def _home_or_refuse(bind, rows: int, what: str, then: str):
    """The home league's key; None only when there is nothing to file (rows and no home league: RuntimeError)."""
    home = bind.execute(sa.text("SELECT key FROM leagues WHERE home")).scalar()
    if rows and home is None:
        raise RuntimeError(f"0022: {rows} league-state and page-cache rows and there is no home league {what}; "
                           f"register it first (`uv run python -m ffl.leagues add espn <id> --home`), then {then}")
    return home


def _rebuild_page_cache(league: bool, where: str, params: dict) -> None:
    """Rebuild page_cache with or without the league key, copying the rows the WHERE keeps."""
    bind = op.get_bind()
    cols = [sa.Column("league", sa.String(), nullable=False)] if league else []
    cols += [sa.Column("season", sa.Integer(), nullable=False), sa.Column("key", sa.String(), nullable=False),
             sa.Column("payload", sa.JSON(), nullable=False), sa.Column("built_at", sa.Float(), nullable=False)]
    pk = ("league", "season", "key") if league else ("season", "key")
    op.create_table("_page_cache_new", *cols, sa.PrimaryKeyConstraint(*pk))
    if league:
        bind.execute(sa.text("INSERT INTO _page_cache_new (league, season, key, payload, built_at) "
                             "SELECT :home, season, key, payload, built_at FROM page_cache ORDER BY rowid"), params)
    else:
        bind.execute(sa.text("INSERT INTO _page_cache_new (season, key, payload, built_at) "
                             f"SELECT season, key, payload, built_at FROM page_cache {where} ORDER BY rowid"), params)
    op.drop_table("page_cache")
    op.rename_table("_page_cache_new", "page_cache")


def upgrade() -> None:
    """Upgrade schema; the moved keys and every cached page are the home league's (the only league season side ran)."""
    bind = op.get_bind()
    moved = bind.execute(sa.text(f"SELECT count(*) FROM notify_state WHERE key IN ({_IN})")).scalar()
    cached = bind.execute(sa.text("SELECT count(*) FROM page_cache")).scalar()
    home = _home_or_refuse(bind, moved + cached, "to file them under", "upgrade")
    op.create_table(
        "league_state",
        sa.Column("league", sa.String(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("league", "season", "key"),
    )
    bind.execute(sa.text("INSERT INTO league_state (league, season, key, value) "
                         f"SELECT :home, season, key, value FROM notify_state WHERE key IN ({_IN})"), {"home": home})
    bind.execute(sa.text(f"DELETE FROM notify_state WHERE key IN ({_IN})"))
    _rebuild_page_cache(True, "", {"home": home})


def downgrade() -> None:
    """Downgrade schema; only the home league's rows fit the old keys, so the others are dropped (with no home
    league every row would be: refused)."""
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT count(*) FROM league_state")).scalar() \
        + bind.execute(sa.text("SELECT count(*) FROM page_cache")).scalar()
    _home_or_refuse(bind, rows, "whose rows to keep", "downgrade")
    bind.execute(sa.text("INSERT OR REPLACE INTO notify_state (season, key, value) "
                         f"SELECT season, key, value FROM league_state WHERE league = {_HOME}"))
    op.drop_table("league_state")
    _rebuild_page_cache(False, f"WHERE league = {_HOME}", {})
