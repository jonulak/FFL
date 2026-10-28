"""per-user data: every personal row is one user's, what ESPN keeps per team is the team's (ffl-soc.6.2.3)

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-27 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0030'
down_revision: Union[str, Sequence[str], None] = '0029'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, its natural key before, after) for the personal tables; decisions keeps its id
PERSONAL = (
    ("watchlist", ("league", "season", "player_id")),
    ("watch_signals", ("league", "season", "key")),
    ("sits", ("league", "season", "week", "player_id")),
    ("notices", ("league", "season", "key")),
)
PER_TEAM = (
    ("claim_plans", ("league", "season", "week")),
    ("seats", ("league", "season", "player_id")),
)
USER_KEYS = ("prefs", "news_mutes", "player_presets")             # notify_state -> user_state
MEMBER_KEYS = ("notify_prefs", "decision_prefs", "watch_state")   # league_state -> member_state
TEAM_KEYS = ("pending_claims", "waiver_run")                      # league_state -> team_state
INDEXES = {  # name: (table, before, after)
    "ix_watch_signals_player": ("watch_signals", ("league", "season", "player_id"),
                                ("user_id", "league", "season", "player_id")),
    "ix_notices_unsent": ("notices", ("league", "season", "sent_at"), ("user_id", "sent_at")),
}


def _pk(table: str) -> str:
    return op.get_bind().execute(sa.text(f"SELECT conname FROM pg_constraint WHERE conrelid = '{table}'::regclass "
                                         "AND contype = 'p'")).scalar()


def _rekey(table: str, cols) -> None:
    op.drop_constraint(_pk(table), table, type_='primary')
    op.create_primary_key(f'{table}_pkey', table, list(cols))


def _personal_rows(bind) -> list[str]:
    """The tables holding a personal row (or a personal state key): what a database with no user cannot file."""
    tables = [t for t, _ in PERSONAL] + ["decisions", "push_subscriptions"]
    have = [t for t in tables if bind.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{t}")')).scalar()]
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM notify_state WHERE key = ANY(:k))"),
                    {"k": list(USER_KEYS)}).scalar():
        have.append("notify_state")
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM league_state WHERE key = ANY(:k))"),
                    {"k": list(MEMBER_KEYS)}).scalar():
        have.append("league_state")
    return have


def upgrade() -> None:
    """Every personal row is filed under the lowest user id, whose they all are (Jonathan's in production), and every
    per-team row under that user's team in its league. A database holding personal rows and no user refuses: they
    would belong to nobody. Per-team rows (and team state) of a league where that user holds no team are dropped:
    no team owns them. So are caps with no user (the migrations seed them from the preset's suggestion, and
    strategy.caps seeds a member's again on first read)."""
    bind = op.get_bind()
    owner = bind.execute(sa.text("SELECT min(id) FROM users")).scalar()
    if owner is None and (have := _personal_rows(bind)):
        raise RuntimeError(f"personal rows in {', '.join(have)} and no user to file them under: add one first "
                           "(`uv run python -m ffl.leagues user add <email>`), then upgrade again")
    fk = dict(ondelete='CASCADE')

    for table, key in PERSONAL:
        op.add_column(table, sa.Column('user_id', sa.Integer(), nullable=True))
        bind.execute(sa.text(f'UPDATE "{table}" SET user_id = :u'), {"u": owner})
        op.alter_column(table, 'user_id', nullable=False)
        op.create_foreign_key(f'fk_{table}_user', table, 'users', ['user_id'], ['id'], **fk)
        _rekey(table, ("user_id", *key))
    for name, (table, _, after) in INDEXES.items():
        op.drop_index(name, table_name=table)
        op.create_index(name, table, list(after))
    for table in ("decisions", "push_subscriptions"):
        op.add_column(table, sa.Column('user_id', sa.Integer(), nullable=True))
        bind.execute(sa.text(f'UPDATE "{table}" SET user_id = :u'), {"u": owner})
        op.alter_column(table, 'user_id', nullable=False)
        op.create_foreign_key(f'fk_{table}_user', table, 'users', ['user_id'], ['id'], **fk)
    op.create_index('ix_decisions_user', 'decisions', ['user_id', 'league', 'season'])
    op.create_index('ix_push_subscriptions_user', 'push_subscriptions', ['user_id'])

    for table, key in PER_TEAM:
        op.add_column(table, sa.Column('team_id', sa.Integer(), nullable=True))
        bind.execute(sa.text(f'UPDATE "{table}" t SET team_id = m.team_id FROM memberships m '
                             'WHERE m.league = t.league AND m.user_id = :u'), {"u": owner})
        bind.execute(sa.text(f'DELETE FROM "{table}" WHERE team_id IS NULL'))
        op.alter_column(table, 'team_id', nullable=False)
        _rekey(table, (*key[:2], "team_id", *key[2:]))

    op.create_table(
        'user_state',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'season', 'key'),
    )
    op.create_table(
        'member_state',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'league'], ['memberships.user_id', 'memberships.league'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'league', 'season', 'key'),
    )
    op.create_table(
        'team_state',
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('season', sa.Integer(), nullable=False),
        sa.Column('team_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(), nullable=False),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('league', 'season', 'team_id', 'key'),
    )
    op.create_table(
        'member_caps',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('league', sa.String(), nullable=False),
        sa.Column('pos', sa.String(), nullable=False),
        sa.Column('my_cap', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id', 'league'], ['memberships.user_id', 'memberships.league'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'league', 'pos'),
    )
    if owner is not None:
        bind.execute(sa.text("INSERT INTO user_state (user_id, season, key, value) "
                             "SELECT :u, season, key, value FROM notify_state WHERE key = ANY(:k)"),
                     {"u": owner, "k": list(USER_KEYS)})
        bind.execute(sa.text("INSERT INTO member_state (user_id, league, season, key, value) "
                             "SELECT m.user_id, s.league, s.season, s.key, s.value FROM league_state s "
                             "JOIN memberships m ON m.league = s.league AND m.user_id = :u WHERE s.key = ANY(:k)"),
                     {"u": owner, "k": list(MEMBER_KEYS)})
        bind.execute(sa.text("INSERT INTO team_state (league, season, team_id, key, value) "
                             "SELECT s.league, s.season, m.team_id, s.key, s.value FROM league_state s "
                             "JOIN memberships m ON m.league = s.league AND m.user_id = :u "
                             "WHERE s.key = ANY(:k) AND m.team_id IS NOT NULL"),
                     {"u": owner, "k": list(TEAM_KEYS)})
        bind.execute(sa.text("INSERT INTO member_caps (user_id, league, pos, my_cap) "
                             "SELECT m.user_id, c.league, c.pos, c.my_cap FROM league_caps c "
                             "JOIN memberships m ON m.league = c.league AND m.user_id = :u WHERE c.my_cap IS NOT NULL"),
                     {"u": owner})
    bind.execute(sa.text("DELETE FROM notify_state WHERE key = ANY(:k)"), {"k": list(USER_KEYS)})
    bind.execute(sa.text("DELETE FROM league_state WHERE key = ANY(:k)"), {"k": list(MEMBER_KEYS + TEAM_KEYS)})
    op.drop_column('league_caps', 'my_cap')


def downgrade() -> None:
    """The lowest user id's rows back into the old shapes (theirs were the only ones before); every other user's
    personal rows are dropped, as are other teams' claim plans and seats."""
    bind = op.get_bind()
    owner = bind.execute(sa.text("SELECT coalesce(min(id), -1) FROM users")).scalar()
    op.add_column('league_caps', sa.Column('my_cap', sa.Integer(), nullable=True))
    bind.execute(sa.text("INSERT INTO league_caps (league, pos, provider_limit, my_cap) "
                         "SELECT league, pos, NULL, my_cap FROM member_caps WHERE user_id = :u "
                         "ON CONFLICT (league, pos) DO UPDATE SET my_cap = EXCLUDED.my_cap"), {"u": owner})
    bind.execute(sa.text("INSERT INTO notify_state (season, key, value) "
                         "SELECT season, key, value FROM user_state WHERE user_id = :u "
                         "ON CONFLICT (season, key) DO UPDATE SET value = EXCLUDED.value"), {"u": owner})
    bind.execute(sa.text("INSERT INTO league_state (league, season, key, value) "
                         "SELECT league, season, key, value FROM member_state WHERE user_id = :u "
                         "ON CONFLICT (league, season, key) DO UPDATE SET value = EXCLUDED.value"), {"u": owner})
    bind.execute(sa.text("INSERT INTO league_state (league, season, key, value) "
                         "SELECT s.league, s.season, s.key, s.value FROM team_state s "
                         "JOIN memberships m ON m.league = s.league AND m.team_id = s.team_id AND m.user_id = :u "
                         "ON CONFLICT (league, season, key) DO UPDATE SET value = EXCLUDED.value"), {"u": owner})
    for t in ('member_caps', 'team_state', 'member_state', 'user_state'):
        op.drop_table(t)

    for table, key in PER_TEAM:
        bind.execute(sa.text(f'DELETE FROM "{table}" t WHERE NOT EXISTS (SELECT 1 FROM memberships m '
                             'WHERE m.league = t.league AND m.team_id = t.team_id AND m.user_id = :u)'), {"u": owner})
        _rekey(table, key)
        op.drop_column(table, 'team_id')

    op.drop_index('ix_push_subscriptions_user', table_name='push_subscriptions')
    op.drop_index('ix_decisions_user', table_name='decisions')
    for table in ("decisions", "push_subscriptions"):
        bind.execute(sa.text(f'DELETE FROM "{table}" WHERE user_id <> :u'), {"u": owner})
        op.drop_constraint(f'fk_{table}_user', table, type_='foreignkey')
        op.drop_column(table, 'user_id')
    for name, (table, before, _) in INDEXES.items():
        op.drop_index(name, table_name=table)
        op.create_index(name, table, list(before))
    for table, key in PERSONAL:
        bind.execute(sa.text(f'DELETE FROM "{table}" WHERE user_id <> :u'), {"u": owner})
        _rekey(table, key)
        op.drop_constraint(f'fk_{table}_user', table, type_='foreignkey')
        op.drop_column(table, 'user_id')
