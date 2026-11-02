"""0038: news_stories gains a nullable vector(384) embedding, and the downgrade drops it. Pinned to 0038 and read with
raw SQL (migration tests pin their revision)."""
import sqlalchemy as sa

from ffl import db


def test_0038_adds_the_embedding_column_and_the_downgrade_drops_it(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0038")
    eng = sa.create_engine(url)
    col = ("SELECT format_type(a.atttypid, a.atttypmod), NOT a.attnotnull FROM pg_attribute a "
           "WHERE a.attrelid = 'news_stories'::regclass AND a.attname = 'embedding' AND NOT a.attisdropped")
    with eng.begin() as c:
        assert tuple(c.execute(sa.text(col)).one()) == ("vector(384)", True)
        c.execute(sa.text("INSERT INTO news_stories (season, first_at, updated_at, latest_at, rev, written_rev, "
                          "reword, headline, overview, kind, status, embedding) VALUES (2026, 'a', 'a', 'a', 1, 1, 0, "
                          "'h', 'o', '', 'live', :v)"), {"v": "[" + ",".join(["0.1"] * 384) + "]"})
    command.downgrade(db._alembic_config(), "0037")
    with eng.connect() as c:
        assert c.execute(sa.text(col)).first() is None
        assert c.execute(sa.text("SELECT count(*) FROM news_stories")).scalar_one() == 1
