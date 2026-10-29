"""0032 (ffl-soc.6.3.3): the pairing tables arrive empty, a user's delete takes their rows, the downgrade drops them.
Pinned to 0032 and read with raw SQL (bd memory migration-tests-pin-their-revision)."""
import sqlalchemy as sa

from ffl import db


def test_0032_adds_the_pairing_tables_and_the_downgrade_drops_them(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0032")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        uid = c.execute(sa.text("INSERT INTO users (email, created_at) VALUES ('me@example.com', 'now') "
                                "RETURNING id")).scalar_one()
        c.execute(sa.text("INSERT INTO espn_pairings (code_hash, poll_hash, label, user_id, created_at, expires_at) "
                          "VALUES ('c', 'p', 'Safari on iPhone', :u, 'now', 'later')"), {"u": uid})
        c.execute(sa.text("INSERT INTO espn_pairings (code_hash, poll_hash, label, created_at, expires_at) "
                          "VALUES ('c2', 'p2', 'Chrome on Mac', 'now', 'later')"))   # unconfirmed: no user
        c.execute(sa.text("INSERT INTO extension_devices (token_hash, user_id, label, created_at) "
                          "VALUES ('t', :u, 'Safari on iPhone', 'now')"), {"u": uid})
        c.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": uid})
        assert c.execute(sa.text("SELECT count(*) FROM extension_devices")).scalar_one() == 0
        assert c.execute(sa.text("SELECT label FROM espn_pairings")).scalars().all() == ["Chrome on Mac"]
    command.downgrade(db._alembic_config(), "0031")
    with eng.connect() as c:
        left = {r[0] for r in c.execute(sa.text(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN "
            "('espn_pairings', 'extension_devices')"))}
    assert left == set()
