"""0033 (#460): espn_credentials becomes one row per ESPN account, an existing row kept as it is (its secret untouched,
swid_hash NULL until the app opens it), memberships gain espn_account; the downgrade keeps each user's first account.
Pinned to 0033 and read with raw SQL (CLAUDE.md: a migration's test upgrades to its own revision)."""
import pytest
import sqlalchemy as sa

from ffl import db


def test_0033_keys_credentials_by_account_and_the_downgrade_keeps_the_first(empty_db, monkeypatch):
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
        c.execute(sa.text("INSERT INTO espn_credentials (user_id, secret, updated_at) VALUES (:u, 'sealed-a', 'now')"),
                  {"u": uid})
    command.upgrade(db._alembic_config(), "0033")
    with eng.begin() as c:
        row = c.execute(sa.text("SELECT id, swid_hash, secret FROM espn_credentials")).one()
        assert row.id is not None and row.swid_hash is None and row.secret == "sealed-a"
        c.execute(sa.text("INSERT INTO espn_credentials (user_id, swid_hash, secret, updated_at) "
                          "VALUES (:u, 'h-b', 'sealed-b', 'now')"), {"u": uid})
        cols = {r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'memberships'"))}
        assert "espn_account" in cols
    with pytest.raises(sa.exc.IntegrityError), eng.begin() as c:   # one row per (user, account)
        c.execute(sa.text("INSERT INTO espn_credentials (user_id, swid_hash, secret, updated_at) "
                          "VALUES (:u, 'h-b', 'again', 'now')"), {"u": uid})
    command.downgrade(db._alembic_config(), "0032")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT user_id, secret FROM espn_credentials")).all() == [(uid, "sealed-a")]
