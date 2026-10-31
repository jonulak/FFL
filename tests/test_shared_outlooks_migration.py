"""0035: outlooks collapses to one row per (season, week, player), the newest of any league or kind.
Pinned to 0035 and read with raw SQL (bd memory migration-tests-pin-their-revision)."""
import sqlalchemy as sa

from ffl import db


def _columns(c):
    return set(c.execute(sa.text("SELECT column_name FROM information_schema.columns "
                                 "WHERE table_name = 'outlooks'")).scalars())


def test_0035_collapses_outlooks_to_one_row_per_player_week(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0034")
    eng = sa.create_engine(url)
    ins = sa.text("INSERT INTO outlooks (league, season, week, kind, player_id, entry, updated) "
                  "VALUES (:l, 2026, 8, :k, :p, CAST(:e AS json), :u)")
    with eng.begin() as c:
        for l, k, p, e, u in [("espn:1", "week", 7, "a", "2026-10-31T01"), ("espn:2", "week", 7, "b", "2026-10-31T02"),
                              ("espn:1", "waivers-week", 7, "c", "2026-10-31T03"), ("espn:1", "week", 9, "d", "2026-10-31T01")]:
            c.execute(ins, {"l": l, "k": k, "p": p, "e": f'"{e}"', "u": u})
    command.upgrade(db._alembic_config(), "0035")
    with eng.connect() as c:
        assert _columns(c) == {"season", "week", "player_id", "entry", "updated"}
        assert c.execute(sa.text("SELECT entry FROM outlooks WHERE player_id = 7")).scalar_one() == "c"
        assert c.execute(sa.text("SELECT count(*) FROM outlooks")).scalar_one() == 2
    command.downgrade(db._alembic_config(), "0034")
    with eng.connect() as c:
        assert {"league", "kind"} <= _columns(c)
