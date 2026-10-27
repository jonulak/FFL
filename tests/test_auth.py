import pytest
import sqlalchemy as sa

from ffl import auth, db, db_backup


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("FFL_SIGNUP_EMAILS", "FFL_JOB_TOKEN", "FFL_DEV_SIGNIN", "FFL_APP_URL"):
        monkeypatch.delenv(k, raising=False)
    auth._local.cache_clear()
    yield
    auth._local.cache_clear()


def _users():
    with db._read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(db.users)).scalar()


def test_signup_closed_by_default():
    assert auth.signup_allowed("a@b.com", True) is False


def test_signup_list_case_insensitive(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", " A@B.com , c@d.com ")
    assert auth.signup_allowed("a@b.COM", True)
    assert auth.signup_allowed("C@d.com", True)
    assert not auth.signup_allowed("x@y.com", True)
    assert not auth.signup_allowed(None, True)


def test_signup_requires_verified(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "a@b.com")
    assert auth.signup_allowed("a@b.com", False) is False


def test_signup_star_opens(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "*")
    assert auth.signup_allowed("anyone@x.com", True)
    assert not auth.signup_allowed("anyone@x.com", False)


def test_sign_in_known_identity_ignores_list(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "a@b.com")
    uid = auth.sign_in("google", "g1", "a@b.com", True, "Ann")
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "")
    assert auth.sign_in("google", "g1", "other@b.com", False, None) == uid


def test_sign_in_claims_existing_user_by_listed_email(monkeypatch):
    uid = db.create_user("a@b.com", "Ann")
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "a@b.com")
    assert auth.sign_in("google", "g1", "A@B.com", True, "Ann") == uid
    assert db.identity_user("google", "g1") == uid
    assert _users() == 1


def test_sign_in_refused_returns_none(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "a@b.com")
    assert auth.sign_in("google", "g2", "x@y.com", True, "X") is None
    assert auth.sign_in("google", "g3", "a@b.com", False, "A") is None
    assert _users() == 0
    assert db.identity_user("google", "g2") is None


def test_job_ok_fails_closed(monkeypatch):
    assert auth.job_ok("x") is False
    assert auth.job_ok(None) is False
    monkeypatch.setenv("FFL_JOB_TOKEN", "")
    assert auth.job_ok("") is False
    monkeypatch.setenv("FFL_JOB_TOKEN", "secret")
    assert auth.job_ok("wrong") is False
    assert auth.job_ok(None) is False
    assert auth.job_ok("secret") is True


def test_dev_signin_needs_local_db(monkeypatch):
    monkeypatch.setenv("FFL_DB_URL", "postgresql+psycopg:///x")
    monkeypatch.setattr(db_backup, "is_local", lambda url: False)
    monkeypatch.setenv("FFL_DEV_SIGNIN", "1")
    assert auth.dev_signin_enabled() is False
    auth._local.cache_clear()
    monkeypatch.setattr(db_backup, "is_local", lambda url: True)
    assert auth.dev_signin_enabled() is True
    monkeypatch.setenv("FFL_DEV_SIGNIN", "0")
    assert auth.dev_signin_enabled() is False


def test_cookie_secure_follows_app_url(monkeypatch):
    assert auth.cookie_secure() is False
    monkeypatch.setenv("FFL_APP_URL", "http://100.1.1.1:3301")
    assert auth.cookie_secure() is False
    monkeypatch.setenv("FFL_APP_URL", "https://ffl.whitebridgelabs.com")
    assert auth.cookie_secure() is True
