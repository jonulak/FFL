"""0040: the notices' read and deleted stamps. Every notice already delivered counts as read, or the first badge is a
season's worth; one still queued stays unread, or its push is lost. Pinned to 0040 and read with raw SQL (migration
tests pin their revision)."""
import sqlalchemy as sa

from ffl import db


def test_0040_marks_delivered_notices_read_and_the_downgrade_drops_the_columns(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0039")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("SET session_replication_role = replica"))  # the rows' user and league do not matter here
        for key, sent in (("sent", "2026-10-01T00:00:00+00:00"), ("queued", None)):
            c.execute(sa.text("INSERT INTO notices (user_id, league, season, key, kind, title, body, url, created_at, sent_at) "
                              "VALUES (1, 'espn:1', 2026, :key, 'move', 't', 'b', '/', '2026-10-01T00:00:00+00:00', :sent)"),
                      {"key": key, "sent": sent})
    command.upgrade(db._alembic_config(), "0040")
    with eng.connect() as c:
        rows = {r[0]: (r[1], r[2]) for r in c.execute(sa.text("SELECT key, read_at, deleted_at FROM notices"))}
    assert rows["sent"][0] is not None and rows["queued"][0] is None
    assert rows["sent"][1] is None
    command.downgrade(db._alembic_config(), "0039")
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'notices'"))}
    assert not cols & {"read_at", "deleted_at"}
