"""ffl-soc.6.2.6: lower(email) unique, expired sessions swept."""
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from ffl import db


def _count(table) -> int:
    with db._read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(table)).scalar()


def test_lower_email_is_unique():
    db.create_user("a@b.com", None)
    with pytest.raises(sa.exc.IntegrityError):
        db.create_user("A@B.com", None)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def test_sweep_sessions_deletes_only_expired():
    uid = db.create_user("a@b.com", None)
    live, dead = db.create_session(uid, ""), db.create_session(uid, "")
    with db._write() as c:
        c.execute(sa.update(db.sessions).where(db.sessions.c.token_hash == db._token_hash(dead))
                  .values(expires_at=_iso(datetime.now(timezone.utc) - timedelta(seconds=1))))
    assert db.sweep_sessions() == 1
    assert db.session_user(live)["id"] == uid
    assert _count(db.sessions) == 1


def test_notify_tick_sweeps_sessions(monkeypatch):
    from fastapi.testclient import TestClient
    from ffl import api
    client = TestClient(api.app)
    monkeypatch.setattr(api.notify, "tick", lambda: {})
    for name in ("correction_pass", "watch_pass", "final_plays"):
        monkeypatch.setattr(api, name, lambda *, ref=None: {})
    monkeypatch.setattr(api, "pregame_pull", lambda now=None, *, ref=None: [])
    monkeypatch.setattr(api.db, "sweep_sessions", lambda: 3)
    assert client.post("/notify/tick").json()["sessions"] == 3


def test_migration_refuses_duplicate_emails(monkeypatch, empty_db):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    db.reset()
    command.upgrade(db._alembic_config(), "0026")
    eng = sa.create_engine(url)                          # not db.engine(): it refuses a schema short of head
    with eng.begin() as c:
        for e in ("a@b.com", "A@B.com"):
            c.execute(sa.text("INSERT INTO users (email, created_at) VALUES (:e, 'now')"), {"e": e})
    eng.dispose()
    with pytest.raises(RuntimeError, match="a@b.com"):
        command.upgrade(db._alembic_config(), "0027")
