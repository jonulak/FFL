import hashlib
from datetime import datetime, timedelta, timezone

import pytest
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


def test_user_by_email_ignores_case():
    uid = db.create_user("Mixed@Example.com", None)
    assert db.user_by_email("mixed@example.COM")["id"] == uid
    assert db.user_by_email("nobody@example.com") is None
    assert db.user_row(uid + 99) is None


def test_user_cascade():
    uid = db.create_user("a@example.com", None)
    token = db.create_session(uid, "")
    with db._write() as c:
        c.execute(sa.delete(db.users).where(db.users.c.id == uid))
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


def test_user_by_username_ignores_case():
    uid = db.create_user(None, None)
    assert db.user_by_username("ann") is None
    db.set_username(uid, "Ann")
    assert db.user_by_username("aNN")["id"] == uid


def test_set_username_taken_in_any_case():
    a, b = db.create_user(None, None), db.create_user(None, None)
    db.set_username(a, "ann")
    with pytest.raises(db.Taken):
        db.set_username(b, "ANN")
    assert db.user_row(b)["username"] is None


def test_set_password_ends_setup():
    uid = db.create_user(None, None)
    assert db.needs_setup()
    db.set_password(uid, "hash")
    assert db.user_row(uid)["password_hash"] == "hash"
    assert not db.needs_setup()


def test_claim_first_user_claims_the_oldest_once():
    with db._write() as c:
        c.execute(sa.delete(db.users))
    first, second = db.create_user("a@example.com", None), db.create_user("b@example.com", None)
    assert db.claim_first_user("ann", "h1") == first
    row = db.user_row(first)
    assert (row["username"], row["password_hash"], row["is_admin"]) == ("ann", "h1", True)
    assert db.claim_first_user("bob", "h2") is None
    assert db.user_row(second)["username"] is None


def test_claim_first_user_inserts_when_there_are_none():
    with db._write() as c:
        c.execute(sa.delete(db.users))
    uid = db.claim_first_user("ann", "h1")
    assert db.user_row(uid)["is_admin"] and db.list_users()[0]["username"] == "ann"
    assert db.claim_first_user("bob", "h2") is None


def test_setup_token_round_trip():
    uid = db.create_user(None, None)
    raw = db.issue_setup_token(uid)
    assert raw not in db.user_row(uid).values()
    assert db.user_by_setup_token(raw)["id"] == uid
    assert db.user_by_setup_token(raw + "x") is None
    with db._write() as c:  # expired
        c.execute(sa.update(db.users).where(db.users.c.id == uid).values(
            setup_expires=_iso(datetime.now(timezone.utc) - timedelta(seconds=1))))
    assert db.user_by_setup_token(raw) is None
    raw = db.issue_setup_token(uid)
    db.clear_setup_token(uid)
    assert db.user_by_setup_token(raw) is None


def test_create_invite_and_duplicate_email():
    uid = db.create_invite("Inv@example.com", "Inv")
    row = db.user_row(uid)
    assert (row["username"], row["password_hash"], row["name"]) == (None, None, "Inv")
    with pytest.raises(db.Taken):
        db.create_invite("inv@EXAMPLE.com", None)
    assert db.create_invite(None, None) != db.create_invite(None, None)  # no email: never a collision


def test_admins_and_list_users():
    with db._write() as c:
        c.execute(sa.delete(db.users))
    a, b = db.create_user(None, "A"), db.create_user("b@example.com", None)
    assert db.admin_count() == 0
    db.set_admin(a, True)
    db.set_password(b, "h")
    assert db.admin_count() == 1
    assert [(u["id"], u["is_admin"], u["has_password"]) for u in db.list_users()] == [(a, True, False), (b, False, True)]
    assert set(db.list_users()[0]) == {"id", "username", "email", "name", "is_admin", "has_password", "created_at"}
    db.set_admin(a, False)
    assert db.admin_count() == 0
