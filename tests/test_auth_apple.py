"""Sign in with Apple (ffl-soc.6.2.5): authlib runs for real against a fake Apple behind httpx (metadata, JWKS, token
endpoint), so the state cookie, the nonce, the token request's body and the id_token check are all the real ones.
The round trip's shape is Apple's: its answer is a form POSTed cross-site, which carries no SameSite=Lax cookie."""
import base64
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.integrations.httpx_client._compat import httpx2 as httpx  # the httpx authlib sends through
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import ECKey, RSAKey

from ffl import api, auth, db

pytestmark = pytest.mark.real_auth

SID, TEAM, KID = "com.example.ffl.web", "TEAM123456", "KEY1234567"
APPLE = "https://appleid.apple.test"
METADATA = {"issuer": auth.APPLE_ISSUER, "authorization_endpoint": APPLE + "/auth/authorize",
            "token_endpoint": APPLE + "/auth/token", "jwks_uri": APPLE + "/auth/keys",
            "response_modes_supported": ["query", "fragment", "form_post"],
            "id_token_signing_alg_values_supported": ["RS256"]}
EC_PRIVATE = ec.generate_private_key(ec.SECP256R1())
P8 = EC_PRIVATE.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
SIGNING = RSAKey.generate_key(2048, parameters={"kid": "apple-test"})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("FFL_DEV_SIGNIN", "FFL_SIGNUP_EMAILS", "FFL_GOOGLE_CLIENT_ID", "FFL_GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FFL_APPLE_CLIENT_ID", SID)
    monkeypatch.setenv("FFL_APPLE_TEAM_ID", TEAM)
    monkeypatch.setenv("FFL_APPLE_KEY_ID", KID)
    monkeypatch.setenv("FFL_APPLE_PRIVATE_KEY", base64.b64encode(P8).decode())
    monkeypatch.setenv("FFL_AUTH_SECRET", "auth-secret-for-tests")
    monkeypatch.setenv("FFL_APP_URL", "https://ffl.test")
    auth._apple.cache_clear()
    yield
    auth._apple.cache_clear()


class FakeApple:
    """Apple's three endpoints at the httpx layer; `claims` is what the next id_token says (nonce filled in)."""

    def __init__(self, claims: dict):
        self.claims = claims
        self.nonce = None
        self.token_requests: list[httpx.Request] = []

    async def send(self, transport, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == auth.APPLE_METADATA:
            return httpx.Response(200, json=METADATA, request=request)
        if url == METADATA["jwks_uri"]:
            return httpx.Response(200, json={"keys": [SIGNING.as_dict(private=False)]}, request=request)
        if url == METADATA["token_endpoint"]:
            self.token_requests.append(request)
            now = int(time.time())
            claims = {"iss": auth.APPLE_ISSUER, "aud": SID, "iat": now, "exp": now + 600,
                      "nonce": self.nonce, **self.claims}
            id_token = jwt.encode({"alg": "RS256", "kid": "apple-test"}, claims, SIGNING)
            return httpx.Response(200, json={"access_token": "a", "token_type": "Bearer", "expires_in": 3600,
                                             "id_token": id_token}, request=request)
        raise AssertionError(f"unexpected request {url}")


def _apple(monkeypatch, claims: dict) -> FakeApple:
    fake = FakeApple(claims)

    async def send(self, request):
        return await fake.send(self, request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)  # under the auth flow
    return fake


def _client() -> TestClient:
    return TestClient(api.app, base_url="https://testserver", raise_server_exceptions=False, follow_redirects=False)


def _authorize(c: TestClient, fake: FakeApple, path: str = "/auth/apple/start?next=/roster") -> dict:
    """Leave for Apple; the authorize URL's query, its nonce kept for the id_token."""
    r = c.get(path)
    assert r.status_code == 302, r.text
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == METADATA["authorization_endpoint"]
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    fake.nonce = q["nonce"]
    return q


def _return(c: TestClient, q: dict, form: dict | None = None):
    """Apple's answer: the form, POSTed with no cookie at all (cross-site, SameSite=Lax), then the 303 it answers
    followed by the browser holding the cookies."""
    bare = _client()
    r = bare.post("/auth/apple/callback", data={"state": q["state"], "code": "apple-code", **(form or {})})
    assert r.status_code == 303
    assert not r.headers.get_list("set-cookie")  # the cookieless POST never touches the round trip's state
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://ffl.test/api/auth/apple/callback"
    return c.get(f"/auth/apple/callback?{loc.query}")


INFO = {"sub": "001234.abcd.0042", "email": "me@example.com", "email_verified": "true", "is_private_email": "false"}
USER = '{"name":{"firstName":"Jon","lastName":"Doe"},"email":"me@example.com"}'


def test_authorize_asks_for_a_form_post_with_name_and_email(monkeypatch):
    fake = _apple(monkeypatch, INFO)
    q = _authorize(_client(), fake)
    assert q["response_mode"] == "form_post" and q["response_type"] == "code"
    assert q["scope"] == "openid name email" and q["client_id"] == SID
    assert q["redirect_uri"] == "https://ffl.test/api/auth/apple/callback"


def test_apple_signs_in_a_listed_email_through_the_post(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    fake = _apple(monkeypatch, INFO)
    c = _client()
    r = _return(c, _authorize(c, fake), {"user": USER})
    assert r.status_code == 302 and r.headers["location"] == "/roster"
    assert any(h.startswith(auth.session_cookie() + "=") for h in r.headers.get_list("set-cookie"))
    uid = db.identity_user("apple", INFO["sub"])
    assert uid is not None and db.user_row(uid)["name"] == "Jon Doe"
    # the secret is in the body, never Basic auth (Apple refuses it), and is Apple's JWT
    (req,) = fake.token_requests
    body = parse_qs(req.content.decode())
    assert "authorization" not in req.headers and body["client_id"] == [SID] and body["code"] == ["apple-code"]
    claims = jwt.decode(body["client_secret"][0], ECKey.import_key(P8)).claims
    assert claims["iss"] == TEAM and claims["sub"] == SID and claims["aud"] == auth.APPLE_ISSUER


def test_a_cookieless_get_is_refused():
    """Without the round trip's cookie the state check fails: this is why the POST hands on to a GET."""
    r = _client().get("/auth/apple/callback", params={"code": "c", "state": "s"})
    assert r.status_code == 302 and r.headers["location"] == "/signin?error=failed"


def test_a_relay_email_off_the_list_is_closed(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    fake = _apple(monkeypatch, {**INFO, "email": "x7q@privaterelay.appleid.com", "is_private_email": "true"})
    c = _client()
    assert _return(c, _authorize(c, fake)).headers["location"] == "/signin?error=closed"
    assert db.identity_user("apple", INFO["sub"]) is None


def test_an_unverified_email_is_closed(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "me@example.com")
    fake = _apple(monkeypatch, {**INFO, "email_verified": "false"})
    c = _client()
    assert _return(c, _authorize(c, fake)).headers["location"] == "/signin?error=closed"


def test_a_cancel_at_apple_fails_cleanly(monkeypatch):
    fake = _apple(monkeypatch, INFO)
    c = _client()
    q = _authorize(c, fake)
    r = _client().post("/auth/apple/callback", data={"state": q["state"], "error": "user_cancelled_authorize"})
    assert r.status_code == 303 and "code=" not in r.headers["location"]
    r = c.get(f"/auth/apple/callback?{urlsplit(r.headers['location']).query}")
    assert r.headers["location"] == "/signin?error=failed" and not fake.token_requests


def test_link_apple_to_a_signed_in_user(monkeypatch):
    """Settings' link: a relay address off the sign-up list still links, since the user is already signed in."""
    fake = _apple(monkeypatch, {**INFO, "email": "x7q@privaterelay.appleid.com", "is_private_email": "true"})
    uid = db.sign_in_user("google", "g-1", "me@example.com", "Me")
    c = _client()
    c.cookies.set(auth.session_cookie(), db.create_session(uid, "pytest"))
    assert c.get("/account").json()["linkable"] == ["apple"]
    r = _return(c, _authorize(c, fake, "/auth/apple/link"))
    assert r.headers["location"] == "/settings?link=linked"
    assert db.identity_user("apple", INFO["sub"]) == uid
    assert c.get("/account").json()["linkable"] == []


def test_apple_needs_all_four_values_and_https(monkeypatch):
    assert auth.apple_enabled()
    assert _client().get("/auth/providers").json()["apple"] is True
    monkeypatch.setenv("FFL_APP_URL", "http://ffl.test")  # Apple takes https return URLs only
    assert not auth.apple_enabled()
    monkeypatch.setenv("FFL_APP_URL", "https://ffl.test")
    for k in ("CLIENT_ID", "TEAM_ID", "KEY_ID", "PRIVATE_KEY"):
        with monkeypatch.context() as m:
            m.setenv(f"FFL_APPLE_{k}", "")
            assert not auth.apple_enabled(), k
            assert _client().get("/auth/apple/start").status_code == 503


def test_the_client_secret_is_rebuilt_daily():
    a = auth._apple(SID, TEAM, KID, base64.b64encode(P8).decode(), 20000)
    b = auth._apple(SID, TEAM, KID, base64.b64encode(P8).decode(), 20001)
    ca = jwt.decode(a.client_secret, ECKey.import_key(P8))
    assert ca.header["alg"] == "ES256" and ca.header["kid"] == KID
    assert ca.claims["iat"] == 20000 * 86400 and ca.claims["exp"] == ca.claims["iat"] + auth.APPLE_SECRET_DAYS * 86400
    assert jwt.decode(b.client_secret, ECKey.import_key(P8)).claims["iat"] == 20001 * 86400


def test_apple_name_reads_the_first_authorizations_user_field():
    assert auth.apple_name(USER) == "Jon Doe"
    assert auth.apple_name('{"name":{"firstName":"Jon"}}') == "Jon"
    for junk in (None, "", "not json", "[]", '{"name":"x"}', '{"name":{}}'):
        assert auth.apple_name(junk) is None, junk


def test_an_unknown_provider_is_404():
    c = _client()
    c.cookies.set(auth.session_cookie(), db.create_session(db.create_user("me@example.com", "Me"), "pytest"))
    assert c.get("/auth/github/link").status_code == 404


def test_apple_unreachable_fails_the_start(monkeypatch):
    """authlib's network errors are httpx2's (auth.NET_ERRORS), not httpx's: an outage reads as failed, not a 500."""
    async def down(self, request):
        raise httpx.ConnectError("down", request=request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", down)
    r = _client().get("/auth/apple/start")
    assert r.status_code == 302 and r.headers["location"] == "/signin?error=failed"
