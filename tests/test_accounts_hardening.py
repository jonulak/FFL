"""ffl-soc.6.2.6: one user per person under concurrent first sign-ins, lower(email) unique, expired sessions swept,
'*' opens sign-up only alone."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from ffl import auth, db
from tests.conftest import TEST_USER


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "a@b.com")


def _count(table) -> int:
    with db._read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(table)).scalar()


def _users() -> int:
    """Users besides the template's signed-in test user."""
    with db._read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(db.users).where(db.users.c.id != TEST_USER["id"])).scalar()


def _race(n, fn):
    """fn(i) on n threads released together; their answers, or the first exception raised."""
    gate, out, errs = threading.Barrier(n), [None] * n, []

    def run(i):
        gate.wait()
        try:
            out[i] = fn(i)
        except Exception as e:  # noqa: BLE001 — surfaced below
            errs.append(e)
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if errs:
        raise errs[0]
    return out


@pytest.fixture
def lockstep(monkeypatch):
    """Every racer reads 'no such identity' (and, in a check-then-insert sign-in, 'no such email') before any of them
    writes: the interleaving that splits a person in two, made certain rather than lucky."""
    def at(n):
        gate = threading.Barrier(n, timeout=5)
        for name in ("identity_user", "user_by_email"):
            real = getattr(db, name)

            def wrapped(*a, real=real, **k):
                r = real(*a, **k)
                try:
                    gate.wait()
                except threading.BrokenBarrierError:
                    pass
                return r
            monkeypatch.setattr(db, name, wrapped)
    return at


def test_concurrent_first_sign_ins_of_one_identity_make_one_user(lockstep):
    lockstep(6)
    uids = _race(6, lambda i: auth.sign_in("google", "g1", "a@b.com", True, "Ann"))
    assert len(set(uids)) == 1 and uids[0] is not None
    assert _users() == 1 and _count(db.identities) == 1


def test_concurrent_first_sign_ins_of_two_identities_one_email_make_one_user(lockstep):
    lockstep(6)
    uids = _race(6, lambda i: auth.sign_in("google" if i % 2 else "apple", f"s{i % 2}", "A@b.com", True, None))
    assert len(set(uids)) == 1
    assert _users() == 1 and _count(db.identities) == 2


def test_sign_in_user_never_moves_an_identity():
    uid = db.sign_in_user("google", "g1", "a@b.com", "Ann")
    other = db.create_user("c@d.com", None)
    with db._write() as c:  # a second identity row pointing at the other user, as a link from 6.2.4 would
        c.execute(sa.insert(db.identities).values(provider="google", subject="g2", user_id=other, email="c@d.com",
                                                 created_at="2026-10-27T00:00:00+00:00"))
    assert db.sign_in_user("google", "g2", "a@b.com", None) == other
    assert db.sign_in_user("google", "g1", "zzz@b.com", None) == uid
    assert _users() == 2                         # a known identity's new email makes no user


def test_ensure_user_matches_any_case():
    uid = db.ensure_user("Dev@Example.com", None)
    assert db.ensure_user("dev@example.COM", "X") == uid
    assert _users() == 1


def test_lower_email_is_unique():
    db.create_user("a@b.com", None)
    with pytest.raises(sa.exc.IntegrityError):
        db.create_user("A@B.com", None)


def test_signup_star_opens_only_alone(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "*, a@b.com")
    assert not auth.signup_allowed("anyone@x.com", True)
    assert auth.signup_allowed("a@b.com", True)
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", " * ")
    assert auth.signup_allowed("anyone@x.com", True)


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
