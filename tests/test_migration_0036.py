"""0036: users gain username, password_hash, is_admin, a setup token; identities goes; email turns nullable and the
oldest user is the admin. Pinned to 0036 and read with raw SQL (migration tests pin their revision)."""
import sqlalchemy as sa

from ffl import db


def test_0036_users_carry_passwords_and_identities_go(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0035")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, email, created_at) VALUES "
                          "(5, 'a@example.com', '2026-10-01T00:00:00+00:00'), "
                          "(9, 'b@example.com', '2026-10-02T00:00:00+00:00')"))
        c.execute(sa.text("INSERT INTO identities (provider, subject, user_id, email, created_at) "
                          "VALUES ('google', 'g1', 5, 'a@example.com', '2026-10-01T00:00:00+00:00')"))
    command.upgrade(db._alembic_config(), "0036")
    with eng.connect() as c:
        cols = {r.column_name: r.is_nullable for r in c.execute(sa.text(
            "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'users'"))}
        assert {"username", "password_hash", "is_admin", "setup_token_hash", "setup_expires"} <= cols.keys()
        assert cols["username"] == "YES" and cols["email"] == "YES"
        assert c.execute(sa.text("SELECT to_regclass('identities')")).scalar_one() is None
        assert dict(c.execute(sa.text("SELECT id, is_admin FROM users")).all()) == {5: True, 9: False}
        idx = c.execute(sa.text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_users_username_lower'")).scalar()
        assert idx and "UNIQUE" in idx and "lower" in idx
