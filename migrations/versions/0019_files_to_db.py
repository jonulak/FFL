"""files to db: the push-tap trace, replay draft logs (FFC + practice) and the props trial in the DB

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-25 12:00:00.000000

"""
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0019'
down_revision: Union[str, Sequence[str], None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LOG_COLS = {"overall", "round", "team_id", "player_id"}  # what makes a CSV a pick log (ffl.replay's need)

_traces = sa.table('traces', sa.column('at', sa.String), sa.column('doc', sa.JSON))
_logs = sa.table('draft_logs', sa.column('source', sa.String), sa.column('log_id', sa.String),
                 sa.column('meta', sa.JSON), sa.column('rows', sa.JSON), sa.column('added_at', sa.String))
_snaps = sa.table('props_snapshots', sa.column('week', sa.Integer), sa.column('kind', sa.String),
                  sa.column('doc', sa.JSON))
_raw = sa.table('props_raw', sa.column('week', sa.Integer), sa.column('kind', sa.String),
                sa.column('event_id', sa.String), sa.column('doc', sa.JSON))


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def _week_kind(name: str) -> tuple[int, str] | None:
    """"week-4-sun" -> (4, "sun")."""
    parts = name.split("-")
    if len(parts) != 3 or parts[0] != "week" or not parts[1].isdigit():
        return None
    return int(parts[1]), parts[2]


def _pick_log(path: Path):
    """The CSV as a pick log's rows, or None when it is not one (no header, or the replay's columns missing)."""
    import pandas as pd
    from ffl.adp import records
    try:
        df = pd.read_csv(path)
    except (ValueError, OSError):
        return None
    return records(df) if _LOG_COLS <= set(df.columns) and len(df) else None


def import_files(bind) -> dict[str, int]:
    """Insert-if-absent from the files these tables replace, read where they are at call time (config.DATA,
    config.JOURNAL): <DATA>/trace.jsonl (only into an empty table: its rows have no natural key),
    <DATA>/ffc/*.csv (source ffc), <JOURNAL>/draft/*.csv pick logs (source practice; the season's own draft is
    draft_picks, filed by 0018), <DATA>/props/week-<n>-<kind>.json and props/raw/week-<n>-<kind>/<event>.json.
    Missing files are skipped; nothing is deleted or moved. Returns the rows added per table."""
    from ffl import config
    data, journal = Path(config.DATA), Path(config.JOURNAL)
    n = {"traces": 0, "draft_logs": 0, "props_snapshots": 0, "props_raw": 0}

    trace = data / "trace.jsonl"
    if trace.exists() and not bind.execute(sa.text("SELECT count(*) FROM traces")).scalar():
        rows = []
        for line in trace.read_text().splitlines():
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict):
                rows.append({"at": str(doc.pop("at", "")), "doc": doc})
        if rows:
            bind.execute(_traces.insert(), rows)
            n["traces"] = len(rows)

    have = {(r.source, r.log_id) for r in bind.execute(sa.text("SELECT source, log_id FROM draft_logs"))}
    logs = []
    for source, folder in (("ffc", data / "ffc"), ("practice", journal / "draft")):
        for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
            if (source, path.stem) in have or path.name == "picks.csv":
                continue
            rows = _pick_log(path)
            if rows is None:
                continue
            meta = {"file": str(path.relative_to(config.ROOT)) if path.is_relative_to(config.ROOT) else str(path)}
            logs.append({"source": source, "log_id": path.stem, "meta": meta, "rows": rows,
                         "added_at": _mtime(path)})
    if logs:
        bind.execute(_logs.insert(), logs)
        n["draft_logs"] = len(logs)

    props = data / "props"
    have = {(r.week, r.kind) for r in bind.execute(sa.text("SELECT week, kind FROM props_snapshots"))}
    snaps = []
    for path in sorted(props.glob("week-*-*.json")) if props.is_dir() else []:
        wk = _week_kind(path.stem)
        if wk is None or wk in have:
            continue
        snaps.append({"week": wk[0], "kind": wk[1], "doc": json.loads(path.read_text())})
    if snaps:
        bind.execute(_snaps.insert(), snaps)
        n["props_snapshots"] = len(snaps)

    have = {(r.week, r.kind, r.event_id)
            for r in bind.execute(sa.text("SELECT week, kind, event_id FROM props_raw"))}
    raw = []
    for folder in sorted((props / "raw").glob("week-*-*")) if (props / "raw").is_dir() else []:
        wk = _week_kind(folder.name)
        if wk is None or not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if (*wk, path.stem) not in have:
                raw.append({"week": wk[0], "kind": wk[1], "event_id": path.stem, "doc": json.loads(path.read_text())})
    if raw:
        bind.execute(_raw.insert(), raw)
        n["props_raw"] = len(raw)
    return n


def upgrade() -> None:
    """Upgrade schema; import the files (see import_files), the last time they are read."""
    op.create_table('draft_logs',
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('log_id', sa.String(), nullable=False),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('rows', sa.JSON(), nullable=False),
        sa.Column('added_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('source', 'log_id'))
    op.create_table('traces',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('at', sa.String(), nullable=False),
        sa.Column('doc', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'))
    op.create_table('props_snapshots',
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('doc', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('week', 'kind'))
    op.create_table('props_raw',
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('doc', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('week', 'kind', 'event_id'))
    got = import_files(op.get_bind())
    if any(got.values()):
        print("0019: imported " + ", ".join(f"{v} {k}" for k, v in got.items() if v))


def downgrade() -> None:
    """Downgrade schema; the files were never removed, so nothing is lost but rows added since."""
    op.drop_table('props_raw')
    op.drop_table('props_snapshots')
    op.drop_table('traces')
    op.drop_table('draft_logs')
