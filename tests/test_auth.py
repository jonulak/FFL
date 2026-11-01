from ffl import appconf, auth


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
    appconf.put(appconf.APP_URL, None)
    assert auth.cookie_secure() is False
    appconf.put(appconf.APP_URL, "http://100.1.1.1:3301")
    assert auth.cookie_secure() is False
    appconf.put(appconf.APP_URL, "https://ffl.whitebridgelabs.com")
    assert auth.cookie_secure() is True
