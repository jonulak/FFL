"""Settings › App: the install's own settings live sealed in the database, never in the environment."""
import pytest
from fastapi.testclient import TestClient

from ffl import api, appconf, db, notify
from tests.conftest import TEST_USER

ME = TEST_USER["id"]
PW = "12345678"


@pytest.fixture
def c():
    db.set_admin(ME, True)
    return TestClient(api.app, raise_server_exceptions=False)


def test_settings_round_trip_without_the_keys_value(c):
    assert c.get("/app/settings").json() == {"app_url": None, "push_contact": None, "youtube_key_set": False,
                                             "transcribe_podcasts": False}
    r = c.post("/app/settings", json={"app_url": "https://ffl.example.com/", "push_contact": "mailto:me@example.com",
                                      "youtube_key": " AIza-secret "})
    assert r.json() == {"app_url": "https://ffl.example.com", "push_contact": "me@example.com", "youtube_key_set": True,
                        "transcribe_podcasts": False}
    assert db.get_secret(appconf.YOUTUBE_KEY) == "AIza-secret" and "AIza" not in c.get("/app/settings").text
    # a field left out is kept; an empty one is cleared
    assert c.post("/app/settings", json={"youtube_key": ""}).json() == {
        "app_url": "https://ffl.example.com", "push_contact": "me@example.com", "youtube_key_set": False,
        "transcribe_podcasts": False}
    # the transcriber switch is a setting too (never .env): on, off, left out
    assert c.post("/app/settings", json={"transcribe_podcasts": True}).json()["transcribe_podcasts"] is True
    assert appconf.get(appconf.TRANSCRIBE) == "1"
    assert c.post("/app/settings", json={"app_url": "https://ffl.example.com"}).json()["transcribe_podcasts"] is True
    assert c.post("/app/settings", json={"transcribe_podcasts": False}).json()["transcribe_podcasts"] is False
    assert appconf.get(appconf.TRANSCRIBE) is None


@pytest.mark.parametrize("body", [{"app_url": "ffl.example.com"}, {"app_url": "ftp://x"},
                                  {"app_url": "https://a b"}, {"push_contact": "admin"},
                                  {"push_contact": "a@b"}])
def test_bad_values_are_refused_and_nothing_is_stored(c, body):
    r = c.post("/app/settings", json={**body, "youtube_key": "k"})
    assert r.status_code == 422 and r.json()["error"] in ("bad_url", "bad_contact")
    assert db.get_secret(appconf.YOUTUBE_KEY) is None


def test_a_non_admin_is_refused(c):
    db.set_admin(ME, False)
    assert c.get("/app/settings").status_code == 403
    assert c.post("/app/settings", json={"app_url": "https://x.example"}).status_code == 403


def test_ensure_keys_makes_a_pair_once(monkeypatch):
    monkeypatch.setenv("FFL_NOTIFY", "1")
    assert notify.sender() is None, "no keys: no push"
    assert notify.ensure_keys() is True
    pub, priv = appconf.get(appconf.VAPID_PUBLIC), appconf.get(appconf.VAPID_PRIVATE)
    assert pub and priv and notify.sender() is notify.webpush_sender
    assert notify.ensure_keys() is False
    assert (appconf.get(appconf.VAPID_PUBLIC), appconf.get(appconf.VAPID_PRIVATE)) == (pub, priv), "kept"


def test_ensure_keys_replaces_a_pair_the_ring_cannot_open(monkeypatch):
    from ffl import vault
    notify.ensure_keys()
    monkeypatch.setenv("FFL_SECRET_KEYS", vault.keygen())  # the key ring was lost
    appconf.forget()
    assert notify.ensure_keys() is True and appconf.get(appconf.VAPID_PRIVATE)


def test_the_push_subject_is_the_contact_else_the_app_url():
    """Apple answers 403 BadJwtToken to a placeholder subject such as mailto:admin@localhost."""
    appconf.put(appconf.APP_URL, "https://ffl.example.com")
    assert notify.vapid_subject() == "https://ffl.example.com"
    appconf.put(appconf.PUSH_CONTACT, "me@example.com")
    assert notify.vapid_subject() == "mailto:me@example.com"


def test_webpush_sender_signs_with_the_stored_key_and_subject(monkeypatch):
    import pywebpush
    seen = {}
    monkeypatch.setattr(pywebpush, "webpush", lambda **kw: seen.update(kw))
    notify.ensure_keys()
    appconf.put(appconf.PUSH_CONTACT, "me@example.com")
    notify.webpush_sender({"endpoint": "https://push/a", "keys": {"p256dh": "k", "auth": "a"}}, {"title": "x"})
    assert seen["vapid_private_key"] == appconf.get(appconf.VAPID_PRIVATE)
    assert seen["vapid_claims"] == {"sub": "mailto:me@example.com"}


def test_setup_stores_where_the_admin_opened_the_app():
    c = TestClient(api.app, raise_server_exceptions=False)
    r = c.post("/setup", json={"username": "jon", "password": PW}, headers={"origin": "https://ffl.example.com"})
    assert r.status_code == 200
    assert appconf.app_url() == "https://ffl.example.com"
    assert "secure" in r.headers["set-cookie"].lower().split("; "), "an https app URL: a Secure cookie at once"
