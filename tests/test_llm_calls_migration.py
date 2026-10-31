"""0034 (#64): llm_calls arrives empty with its indexes, takes a row, and the downgrade drops it.
Pinned to 0034 and read with raw SQL (bd memory migration-tests-pin-their-revision)."""
import sqlalchemy as sa

from ffl import db


def test_0034_adds_llm_calls_and_the_downgrade_drops_it(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0034")
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO llm_calls (at, kind, model, input, output, cost_usd, ok) "
                          "VALUES ('2026-10-31T12:00:00+00:00', 'news', 'haiku', 5, 1, 0.001, true)"))
        assert c.execute(sa.text("SELECT league, player_id FROM llm_calls")).one() == (None, None)
        idx = set(c.execute(sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'llm_calls'")).scalars())
        assert {"ix_llm_calls_at", "ix_llm_calls_kind"} <= idx
    command.downgrade(db._alembic_config(), "0033")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT to_regclass('llm_calls')")).scalar_one() is None
