from ffl import auth


def test_job_ok_fails_closed(monkeypatch):
    monkeypatch.delenv("FFL_JOB_TOKEN", raising=False)
    assert auth.job_ok("x") is False
    assert auth.job_ok(None) is False
    monkeypatch.setenv("FFL_JOB_TOKEN", "")
    assert auth.job_ok("") is False
    monkeypatch.setenv("FFL_JOB_TOKEN", "secret")
    assert auth.job_ok("wrong") is False
    assert auth.job_ok(None) is False
    assert auth.job_ok("secret") is True


def test_cookie_secure_follows_app_url(monkeypatch):
    monkeypatch.delenv("FFL_APP_URL", raising=False)
    assert auth.cookie_secure() is False
    monkeypatch.setenv("FFL_APP_URL", "http://100.1.1.1:3301")
    assert auth.cookie_secure() is False
    monkeypatch.setenv("FFL_APP_URL", "https://ffl.whitebridgelabs.com")
    assert auth.cookie_secure() is True
