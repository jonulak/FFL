"""no implicit league: the home league's push switches into its own state; leagues.home and leagues.team_id dropped
(ffl-soc.6.2.2 B, ffl-soc.4.12)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-27 21:00:00.000000

"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0029'
down_revision: Union[str, Sequence[str], None] = '0028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SWITCHES = ("move", "injury", "waivers", "news", "watch")


def upgrade() -> None:
    """The home league's push switches lived in the global `prefs` row beside the quiet hours; every other league's in
    its own `notify_prefs` state. Home's move to its own (a row it already has is kept), the global row keeps the quiet
    hours alone. A member's team lives on the membership (0028 copied it there), and no league is home any more."""
    bind = op.get_bind()
    home = bind.execute(sa.text("SELECT key FROM leagues WHERE home")).scalar()
    rows = bind.execute(sa.text("SELECT season, value FROM notify_state WHERE key = 'prefs'")).all()
    for season, value in rows:
        value = value or {}
        switches = {k: value[k] for k in SWITCHES if k in value}
        if home is not None and switches:
            bind.execute(sa.text(
                "INSERT INTO league_state (league, season, key, value) VALUES (:l, :s, 'notify_prefs', CAST(:v AS JSON)) "
                "ON CONFLICT (league, season, key) DO NOTHING"),
                {"l": home, "s": season, "v": _json(switches)})
        rest = {k: v for k, v in value.items() if k not in SWITCHES}
        bind.execute(sa.text("UPDATE notify_state SET value = CAST(:v AS JSON) WHERE key = 'prefs' AND season = :s"),
                     {"v": _json(rest), "s": season})
    op.drop_column('leagues', 'home')
    op.drop_column('leagues', 'team_id')


def downgrade() -> None:
    """The columns back, team and home from the owner's membership (its default is home; no user: the oldest league,
    which home always was); the home league's switches back into the global row."""
    op.add_column('leagues', sa.Column('team_id', sa.Integer(), nullable=True))
    op.add_column('leagues', sa.Column('home', sa.Boolean(), server_default=sa.false(), nullable=False))
    bind = op.get_bind()
    owner = bind.execute(sa.text("SELECT min(id) FROM users")).scalar()
    if owner is not None:
        bind.execute(sa.text("UPDATE leagues SET team_id = m.team_id, home = m.is_default FROM memberships m "
                             "WHERE m.league = leagues.key AND m.user_id = :u"), {"u": owner})
    else:
        bind.execute(sa.text("UPDATE leagues SET home = true WHERE key = "
                             "(SELECT key FROM leagues ORDER BY added_at, key LIMIT 1)"))
    home = bind.execute(sa.text("SELECT key FROM leagues WHERE home")).scalar()
    if home is None:
        return
    for season, value in bind.execute(sa.text(
            "SELECT season, value FROM league_state WHERE league = :l AND key = 'notify_prefs'"), {"l": home}).all():
        cur = bind.execute(sa.text("SELECT value FROM notify_state WHERE key = 'prefs' AND season = :s"),
                           {"s": season}).scalar() or {}
        merged = {**cur, **{k: v for k, v in (value or {}).items() if k in SWITCHES}}
        bind.execute(sa.text(
            "INSERT INTO notify_state (season, key, value) VALUES (:s, 'prefs', CAST(:v AS JSON)) "
            "ON CONFLICT (season, key) DO UPDATE SET value = EXCLUDED.value"), {"s": season, "v": _json(merged)})
        bind.execute(sa.text("DELETE FROM league_state WHERE league = :l AND season = :s AND key = 'notify_prefs'"),
                     {"l": home, "s": season})


def _json(v) -> str:
    return json.dumps(v)
