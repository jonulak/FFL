"""drop source_weights: per-source weights are gone (the fits sat within 0.01 of equal); every preset whose prose
still describes them gets a new version without that sentence

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-18 12:00:00.000000
"""
import json
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0041'
down_revision: Union[str, Sequence[str], None] = '0040'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = (", and `ffl.calibrate sources` fits per-source weights\nWednesdays against what happened; the weights are stored "
       "inactive and apply only once activated by hand.")
NEW = ", so a finished week's board shows what each source said."

_LATEST = sa.text("SELECT s.key, s.version, s.name, s.prose, s.constants FROM strategies s "
                  "WHERE s.version = (SELECT max(version) FROM strategies WHERE key = s.key)")


def upgrade() -> None:
    op.drop_table('source_weights')
    c = op.get_bind()
    at = datetime.now().astimezone().isoformat(timespec="seconds")
    for key, version, name, prose, constants in c.execute(_LATEST).all():
        if OLD in prose:
            c.execute(sa.text("INSERT INTO strategies (key, version, name, prose, constants, added_at) "
                              "VALUES (:k, :v, :n, :p, CAST(:c AS json), :at)"),
                      {"k": key, "v": version + 1, "n": name, "p": prose.replace(OLD, NEW),
                       "c": json.dumps(constants), "at": at})


def downgrade() -> None:
    c = op.get_bind()
    for key, version, _, prose, _ in c.execute(_LATEST).all():
        prev = c.execute(sa.text("SELECT prose FROM strategies WHERE key = :k AND version = :v"),
                         {"k": key, "v": version - 1}).scalar()
        if prev is not None and OLD in prev and prev.replace(OLD, NEW) == prose:
            c.execute(sa.text("DELETE FROM strategies WHERE key = :k AND version = :v"), {"k": key, "v": version})
    op.create_table('source_weights',
                    sa.Column('season', sa.Integer(), nullable=False),
                    sa.Column('fitted_at', sa.String(), nullable=False),
                    sa.Column('fit', sa.JSON(), nullable=False),
                    sa.Column('active', sa.Boolean(), server_default=sa.false(), nullable=False),
                    sa.PrimaryKeyConstraint('season', 'fitted_at'))
