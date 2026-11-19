"""0041: source_weights is dropped and each preset gets a version whose prose no longer describes fitted weights; the
downgrade puts both back. Pinned to 0041 and read with raw SQL (migration tests pin their revision)."""
import sqlalchemy as sa

from ffl import db


def _latest(c):
    return {k: (v, p) for k, v, p in c.execute(sa.text(
        "SELECT key, version, prose FROM strategies s WHERE version = (SELECT max(version) FROM strategies WHERE key = s.key)"))}


def _has_table(c):
    return c.execute(sa.text("SELECT to_regclass('source_weights')")).scalar() is not None


def test_0041_drops_source_weights_and_versions_the_prose(empty_db, monkeypatch):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0040")
    eng = sa.create_engine(url)
    with eng.connect() as c:
        before = _latest(c)
    assert before and all("calibrate sources" in p for _, p in before.values())
    command.upgrade(db._alembic_config(), "0041")
    with eng.connect() as c:
        after = _latest(c)
        assert not _has_table(c)
    assert {k: v for k, (v, _) in after.items()} == {k: v + 1 for k, (v, _) in before.items()}
    assert all("calibrate sources" not in p and "a finished week's board" in p for _, p in after.values())
    command.downgrade(db._alembic_config(), "0040")
    with eng.connect() as c:
        assert _latest(c) == before
        assert _has_table(c)
