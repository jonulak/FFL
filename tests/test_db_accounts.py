import hashlib
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from ffl import db


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _session_row(token):
    h = hashlib.sha256(token.encode()).hexdigest()
    with db._read() as c:
        return c.execute(sa.select(db.sessions).where(db.sessions.c.token_hash == h)).mappings().one()


def _set(token, **vals):
    h = hashlib.sha256(token.encode()).hexdigest()
    with db._write() as c:
        c.execute(sa.update(db.sessions).where(db.sessions.c.token_hash == h).values(**vals))


def test_session_round_trip():
    uid = db.create_user("a@example.com", "Ann")
    token = db.create_session(uid, "UA/1")
    u = db.session_user(token)
    assert u["id"] == uid and u["email"] == "a@example.com" and u["plan"] == "free"
    assert db.user_row(uid)["name"] == "Ann"
    row = _session_row(token)
    assert token not in row.values()
    assert row["ua"] == "UA/1"
    assert db.session_user(token + "x") is None


def test_session_expired():
    uid = db.create_user("a@example.com", None)
    token = db.create_session(uid, "")
    _set(token, expires_at=_iso(datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert db.session_user(token) is None


def test_session_slides():
    uid = db.create_user("a@example.com", None)
    token = db.create_session(uid, "")
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    _set(token, last_seen=_iso(old), expires_at=_iso(old + timedelta(days=90)))
    before = _session_row(token)["expires_at"]
    assert db.session_user(token)["id"] == uid
    after = _session_row(token)
    assert after["expires_at"] > before
    assert after["last_seen"] > _iso(old)


def test_session_within_hour_does_not_write():
    uid = db.create_user("a@example.com", None)
    token = db.create_session(uid, "")
    before = _session_row(token)
    db.session_user(token)
    assert _session_row(token) == before


def test_delete_session():
    uid = db.create_user("a@example.com", None)
    token = db.create_session(uid, "")
    db.delete_session(token)
    assert db.session_user(token) is None


def test_identity_link_and_lookup():
    uid = db.create_user("a@example.com", None)
    assert db.identity_user("google", "123") is None
    db.link_identity(uid, "google", "123", "a@example.com")
    db.link_identity(uid, "google", "123", "a@example.com")  # a repeat is a no-op
    assert db.identity_user("google", "123") == uid
    assert db.identity_user("google", "456") is None


def test_user_by_email_ignores_case():
    uid = db.create_user("Mixed@Example.com", None)
    assert db.user_by_email("mixed@example.COM")["id"] == uid
    assert db.user_by_email("nobody@example.com") is None
    assert db.user_row(uid + 99) is None


def test_user_cascade():
    uid = db.create_user("a@example.com", None)
    db.link_identity(uid, "google", "123", "a@example.com")
    token = db.create_session(uid, "")
    with db._write() as c:
        c.execute(sa.delete(db.users).where(db.users.c.id == uid))
    assert db.identity_user("google", "123") is None
    assert db.session_user(token) is None
    with db._read() as c:
        assert c.execute(sa.select(sa.func.count()).select_from(db.sessions)).scalar() == 0


def test_session_lookup_reads_without_a_write_transaction(monkeypatch):
    """Within the hour a lookup is a read only: no write transaction is opened (the middleware runs it per request)."""
    uid = db.create_user("a@example.com", None)
    token = db.create_session(uid, "")
    writes = []
    real = db._write

    def counting():
        writes.append(1)
        return real()
    monkeypatch.setattr(db, "_write", counting)
    assert db.session_lookup(token) == (db.user_row(uid), False)
    assert db.session_lookup(token + "x") == (None, False)
    assert writes == []
    _set(token, last_seen=_iso(datetime.now(timezone.utc) - timedelta(hours=2)))
    writes.clear()
    assert db.session_lookup(token)[1] is True  # over the hour: the one write, the slide
    assert writes == [1]
