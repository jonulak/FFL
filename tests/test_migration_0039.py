"""0039: news_item_chunks, a NOT NULL vector(384) per piece of an item's text, and the downgrade drops it. Pinned to
0039 and read with raw SQL (migration tests pin their revision)."""
import sqlalchemy as sa

from ffl import db


def test_0039_adds_the_chunks_table_and_the_downgrade_drops_it(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0039")
    eng = sa.create_engine(url)
    col = ("SELECT format_type(a.atttypid, a.atttypmod), a.attnotnull FROM pg_attribute a "
           "WHERE a.attrelid = 'news_item_chunks'::regclass AND a.attname = 'embedding' AND NOT a.attisdropped")
    with eng.begin() as c:
        assert tuple(c.execute(sa.text(col)).one()) == ("vector(384)", True)
        c.execute(sa.text("INSERT INTO news_item_chunks (season, item_id, pos, hash, text, embedding) "
                          "VALUES (2026, 1, 0, 'h', 't', :v)"), {"v": "[" + ",".join(["0.1"] * 384) + "]"})
    command.downgrade(db._alembic_config(), "0038")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT to_regclass('news_item_chunks')")).scalar() is None
