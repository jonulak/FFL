"""0031 (ffl-soc.3): push keys are sealed in place, the ESPN credentials table arrives empty; the downgrade opens the
keys back exactly. Pinned to 0031 and read with raw SQL (bd memory migration-tests-pin-their-revision)."""
import json

import pytest
import sqlalchemy as sa

from ffl import db, vault

KEYS = '{"p256dh": "BPk-secret", "auth": "auth-secret"}'


def _at_0030(empty_db, monkeypatch, rows: bool = True) -> sa.Engine:
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0030")
    eng = sa.create_engine(url)
    if rows:
        with eng.begin() as c:
            uid = c.execute(sa.text("INSERT INTO users (email, created_at) VALUES ('me@example.com', 'now') "
                                    "RETURNING id")).scalar_one()
            c.execute(sa.text("INSERT INTO push_subscriptions (endpoint, keys, ua, created_at, user_id) "
                              "VALUES ('https://push/me', CAST(:k AS JSON), 'iPhone', '2026-10-01', :u)"),
                      {"k": KEYS, "u": uid})
    return eng


def _keys(eng) -> str:
    with eng.connect() as c:
        return c.execute(sa.text("SELECT keys::text FROM push_subscriptions")).scalar_one()


def test_0031_seals_push_keys_and_the_downgrade_opens_them(empty_db, monkeypatch):
    from alembic import command
    eng = _at_0030(empty_db, monkeypatch)
    command.upgrade(db._alembic_config(), "0031")
    sealed = _keys(eng)
    assert "secret" not in sealed and "p256dh" not in sealed
    assert vault.open_(sealed, aad="push_subscriptions:https://push/me:keys") == KEYS
    with eng.connect() as c:
        cols = {r[0] for r in c.execute(sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'espn_credentials'"))}
    assert {"user_id", "secret", "updated_at", "last_ok", "failing_since", "failing_reason"} <= cols
    command.downgrade(db._alembic_config(), "0030")
    assert json.loads(_keys(eng)) == json.loads(KEYS)


def test_0031_without_a_ring_refuses_and_leaves_0030(empty_db, monkeypatch):
    from alembic import command
    eng = _at_0030(empty_db, monkeypatch)
    monkeypatch.delenv("FFL_SECRET_KEYS")
    with pytest.raises(vault.VaultError):
        command.upgrade(db._alembic_config(), "0031")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0030"
    assert json.loads(_keys(eng)) == json.loads(KEYS)


def test_0031_without_a_ring_or_push_rows_upgrades(empty_db, monkeypatch):
    from alembic import command
    eng = _at_0030(empty_db, monkeypatch, rows=False)
    monkeypatch.delenv("FFL_SECRET_KEYS")
    command.upgrade(db._alembic_config(), "0031")
    with eng.connect() as c:
        assert c.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0031"
