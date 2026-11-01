"""0037: app_secrets arrives empty, takes a row, and the downgrade drops it. Pinned to 0037 and read with raw SQL
(migration tests pin their revision)."""
import sqlalchemy as sa

from ffl import db


def test_0037_adds_app_secrets_and_the_downgrade_drops_it(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0037")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        cols = {r.column_name: (r.data_type, r.is_nullable) for r in c.execute(sa.text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'app_secrets'"))}
        assert cols == {"key": ("text", "NO"), "value": ("text", "NO"), "updated_at": ("text", "NO")}
        pk = c.execute(sa.text("SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                               "AND a.attnum = ANY(i.indkey) WHERE i.indrelid = 'app_secrets'::regclass "
                               "AND i.indisprimary")).scalars().all()
        assert pk == ["key"]
        c.execute(sa.text("INSERT INTO app_secrets VALUES ('claude_token', 'sealed', '2026-10-31T00:00:00+00:00')"))
    command.downgrade(db._alembic_config(), "0036")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT to_regclass('app_secrets')")).scalar_one() is None
