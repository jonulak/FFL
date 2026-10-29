"""Connecting ESPN (ffl-soc.6.3.3): the extension's pairing, the cookies it sends, the manual paste, status, devices
and disconnect, through the real middleware (`real_auth`): three routes are public and must hold by their own secret.
ESPN is never reached: the fan API and the league read are stubbed."""
import pytest
from fastapi.testclient import TestClient

from ffl import api, auth, config, db, espn_auth, espn_connect

pytestmark = pytest.mark.real_auth

SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"
OTHER_SWID = "{11111111-2222-3333-4444-555555555555}"
S2 = "AEB" + "x" * 150 + "%2B" + "y" * 100 + "%3D"   # URL-encoded, as the cookie holds it


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FFL_JOB_TOKEN", "job-token-for-tests")
    for w in espn_connect._limits():
        w.clear()


@pytest.fixture
def espn(monkeypatch):
    """ESPN as a stub: the fan API lists `entries`; a league read with no cookies is refused (private, 401) and one
    with cookies passes unless `refuse` is a status. `calls` records (league, cookies or None)."""
    state = {"entries": [espn_connect.Entry("100000001", 7, config.SEASON, "Home", 10)], "refuse": None,
             "public": set(), "calls": []}

    def fan(swid):
        state["calls"].append(("fan", swid))
        return state["entries"]

    def read(entry, jar):
        state["calls"].append((entry.league_id, jar))
        if jar is None:
            return 200 if entry.league_id in state["public"] else 401
        return state["refuse"] or 200

    monkeypatch.setattr(espn_connect, "fan_entries", fan)
    monkeypatch.setattr(espn_connect, "_league_status", read)
    return state


def _client() -> TestClient:
    return TestClient(api.app, raise_server_exceptions=False)


def _signed_in(user_id: int) -> TestClient:
    c = _client()
    c.cookies.set(auth.session_cookie(), db.create_session(user_id, "pytest"))
    return c


def _pair(app: TestClient, ext: TestClient, ua: str = "Mozilla/5.0 (iPhone) Safari/604.1") -> str:
    """Start in the extension, confirm in the app, poll: the device token."""
    start = ext.post("/espn/pair/start", headers={"user-agent": ua}).json()
    assert app.post("/espn/pair/confirm", json={"code": start["code"]}).status_code == 200
    r = ext.post("/espn/pair/poll", json={"poll_secret": start["poll_secret"]}).json()
    assert r["status"] == "connected"
    return r["device_token"]


# --- pairing ---

def test_pairing_start_confirm_poll():
    uid = db.create_user("me@example.com", "Me")
    app, ext = _signed_in(uid), _client()
    r = ext.post("/espn/pair/start", headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7) Safari/604.1"})
    assert r.status_code == 200
    start = r.json()
    code = start["code"]
    assert len(code) == 9 and code[4] == "-" and set(code.replace("-", "")) <= set(espn_connect.CODE_ALPHABET)
    assert ext.post("/espn/pair/poll", json={"poll_secret": start["poll_secret"]}).json() == {"status": "pending"}
    typed = code.lower().replace("-", " ")   # typed loosely
    assert app.post("/espn/pair/confirm", json={"code": typed}).json() == {"ok": True, "label": "Safari on iPhone"}
    done = ext.post("/espn/pair/poll", json={"poll_secret": start["poll_secret"]}).json()
    assert done["status"] == "connected" and done["device_token"].startswith("ffld_")
    # the token is handed over once; the pairing is gone
    assert ext.post("/espn/pair/poll", json={"poll_secret": start["poll_secret"]}).status_code == 410
    devices = app.get("/espn/devices").json()["devices"]
    assert [d["label"] for d in devices] == ["Safari on iPhone"] and devices[0]["id"] == done["device_id"]


def test_a_code_is_confirmed_once_and_never_by_a_second_user():
    a, b = db.create_user("a@example.com", None), db.create_user("b@example.com", None)
    start = _client().post("/espn/pair/start").json()
    assert _signed_in(a).post("/espn/pair/confirm", json={"code": start["code"]}).status_code == 200
    assert _signed_in(b).post("/espn/pair/confirm", json={"code": start["code"]}).status_code == 404
    token = _client().post("/espn/pair/poll", json={"poll_secret": start["poll_secret"]}).json()["device_token"]
    assert db.device_user(token)[1] == a


def test_an_expired_code_is_not_confirmed_and_its_poll_is_gone():
    uid = db.create_user("me@example.com", None)
    db.create_pairing("ABCDEFGH", "poll-old", "Chrome on Mac", -1)
    assert _signed_in(uid).post("/espn/pair/confirm", json={"code": "ABCD-EFGH"}).status_code == 404
    assert _client().post("/espn/pair/poll", json={"poll_secret": "poll-old"}).status_code == 410
    assert db.sweep_pairings() == 1


def test_wrong_codes_stop_after_five_even_for_the_right_one():
    uid = db.create_user("me@example.com", None)
    app = _signed_in(uid)
    start = _client().post("/espn/pair/start").json()
    for _ in range(5):
        assert app.post("/espn/pair/confirm", json={"code": "ZZZZ-ZZZZ"}).status_code == 404
    assert app.post("/espn/pair/confirm", json={"code": start["code"]}).status_code == 429
    # another user's limit is their own
    assert _signed_in(db.create_user("b@example.com", None)).post(
        "/espn/pair/confirm", json={"code": start["code"]}).status_code == 200


def test_starts_are_capped_per_address_and_overall(monkeypatch):
    ext = _client()
    for _ in range(10):
        assert ext.post("/espn/pair/start", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 200
    assert ext.post("/espn/pair/start", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 429
    monkeypatch.setattr(espn_connect, "OPEN_PAIRINGS_MAX", 10)
    r = ext.post("/espn/pair/start", headers={"x-forwarded-for": "5.6.7.8"})
    assert r.status_code == 429 and r.json()["detail"]["error"] == "busy"


def test_confirm_needs_a_session_and_the_job_token_is_not_one():
    start = _client().post("/espn/pair/start").json()
    assert _client().post("/espn/pair/confirm", json={"code": start["code"]}).status_code == 401
    job = {auth.JOB_HEADER: "job-token-for-tests"}
    for m, path in (("POST", "/espn/pair/confirm"), ("POST", "/espn/connect"), ("GET", "/espn/status"),
                    ("GET", "/espn/devices"), ("POST", "/espn/devices/1/revoke"), ("POST", "/espn/disconnect")):
        assert _client().request(m, path, headers=job, json={"code": start["code"]}).status_code == 401, path


def test_poll_with_an_unknown_secret_or_no_body():
    ext = _client()
    assert ext.post("/espn/pair/poll", json={"poll_secret": "nope"}).status_code == 410
    assert ext.post("/espn/pair/poll", content=b"not json").status_code == 400


# --- the cookies ---

def test_the_extension_sends_cookies_and_they_are_stored_raw(espn):
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    token = _pair(app, ext)
    r = ext.post("/espn/cookies", headers={"authorization": f"Bearer {token}"},
                 json={"espn_s2": S2, "swid": "%7BAAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE%7D"})
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True and body["proven"] is True and body["devices"] == 1
    assert S2 not in r.text and "AAAAAAAA" not in r.text
    row = db.credential_row(uid)
    assert row["espn_s2"] == S2 and row["swid"] == SWID   # espn_s2 never decoded; SWID normalised
    assert ("100000001", {"espn_s2": S2, "SWID": SWID}) in espn["calls"]
    st = app.get("/espn/status").json()
    assert st["connected"] is True and st["devices"] == 1 and "espn_s2" not in st and "swid" not in st


def test_cookies_need_a_paired_extension(espn):
    ext = _client()
    body = {"espn_s2": S2, "swid": SWID}
    assert ext.post("/espn/cookies", json=body).status_code == 401
    assert ext.post("/espn/cookies", headers={"authorization": "Bearer ffld_nope"}, json=body).status_code == 401
    # a session is not a device token
    assert _signed_in(db.create_user("me@example.com", None)).post("/espn/cookies", json=body).status_code == 401
    assert espn["calls"] == []


def test_a_bad_body_echoes_nothing(espn):
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    token = _pair(app, ext)
    r = ext.post("/espn/cookies", headers={"authorization": f"Bearer {token}"}, json={"espn_s2": S2, "swid": 7})
    assert r.status_code == 400 and S2 not in r.text
    r = app.post("/espn/connect", json={"espn_s2": ["x" * 120], "swid": SWID})
    assert r.status_code == 400 and "xxxx" not in r.text and "AAAAAAAA" not in r.text


@pytest.mark.parametrize("status", [401, 403])
def test_refused_cookies_store_nothing(espn, status):
    uid = db.create_user("me@example.com", None)
    espn["refuse"] = status
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2, "swid": SWID})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "refused"
    assert db.credential_row(uid) is None


def test_espn_down_stores_nothing(espn):
    uid = db.create_user("me@example.com", None)
    espn["refuse"] = 500
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2, "swid": SWID})
    assert r.status_code == 502 and r.json()["detail"]["error"] == "espn_unavailable"
    assert db.credential_row(uid) is None


def test_public_leagues_prove_nothing_and_the_login_is_stored_unproven(espn):
    uid = db.create_user("me@example.com", None)
    espn["entries"] = [espn_connect.Entry(str(i), 1, config.SEASON, "L", 10) for i in range(40)]
    espn["public"] = {str(i) for i in range(40)}
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2, "swid": SWID})
    assert r.status_code == 200 and r.json()["proven"] is False
    assert db.credential_row(uid)["swid"] == SWID
    assert len([c for c in espn["calls"] if c[0] != "fan"]) == espn_connect.CHECK_LEAGUES   # never all 40


def test_no_leagues_or_a_bad_swid_is_refused(espn):
    app = _signed_in(db.create_user("me@example.com", None))
    espn["entries"] = []
    assert app.post("/espn/connect", json={"espn_s2": S2, "swid": SWID}).json()["detail"]["error"] == "no_leagues"
    r = app.post("/espn/connect", json={"espn_s2": S2, "swid": "AAAAAAAA-anon"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "bad_swid"
    assert app.post("/espn/connect", json={"espn_s2": "short", "swid": SWID}).json()["detail"]["error"] == "bad_espn_s2"


def test_a_different_espn_account_is_refused_before_espn_is_asked(espn):
    uid = db.create_user("me@example.com", None)
    espn_auth.connect(uid, S2, SWID)
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2 + "z", "swid": OTHER_SWID})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "different_account"
    assert espn["calls"] == []
    assert db.credential_row(uid)["espn_s2"] == S2
    # the same account in another case or encoding is an update
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2 + "z", "swid": SWID.lower()})
    assert r.status_code == 200 and db.credential_row(uid)["espn_s2"] == S2 + "z"


def test_new_cookies_clear_a_failing_mark(espn):
    uid = db.create_user("me@example.com", None)
    espn_auth.connect(uid, S2, SWID)
    espn_auth.refused(uid, "ESPN refused a shared read")
    app = _signed_in(uid)
    st = app.get("/espn/status").json()
    assert st["connected"] is False and st["failing_reason"] == "ESPN refused a shared read"
    assert app.post("/espn/connect", json={"espn_s2": S2, "swid": SWID}).status_code == 200
    st = app.get("/espn/status").json()
    assert st["connected"] is True and st["failing_since"] is None


def test_sends_are_capped_per_extension(espn, monkeypatch):
    monkeypatch.setattr(espn_connect, "SENDS", espn_connect.Window(2, 3600))
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    h = {"authorization": f"Bearer {_pair(app, ext)}"}
    codes = [ext.post("/espn/cookies", headers=h, json={"espn_s2": S2, "swid": SWID}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --- devices and disconnect ---

def test_revoking_an_extension_stops_its_sends_and_never_reaches_another_users(espn):
    a, b = db.create_user("a@example.com", None), db.create_user("b@example.com", None)
    app_a, app_b, ext = _signed_in(a), _signed_in(b), _client()
    token = _pair(app_a, ext)
    did = app_a.get("/espn/devices").json()["devices"][0]["id"]
    assert app_b.post(f"/espn/devices/{did}/revoke").status_code == 404
    assert app_b.get("/espn/devices").json() == {"devices": []}
    assert app_a.post(f"/espn/devices/{did}/revoke").json() == {"ok": True}
    assert ext.post("/espn/cookies", headers={"authorization": f"Bearer {token}"},
                    json={"espn_s2": S2, "swid": SWID}).status_code == 401


def test_disconnect_deletes_the_cookies_and_unpairs_every_extension(espn):
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    token = _pair(app, ext)
    h = {"authorization": f"Bearer {token}"}
    assert ext.post("/espn/cookies", headers=h, json={"espn_s2": S2, "swid": SWID}).status_code == 200
    assert app.post("/espn/disconnect").json() == {"ok": True}
    assert db.credential_row(uid) is None and app.get("/espn/devices").json() == {"devices": []}
    assert ext.post("/espn/cookies", headers=h, json={"espn_s2": S2, "swid": SWID}).status_code == 401
    # then another ESPN account connects
    assert app.post("/espn/connect", json={"espn_s2": S2, "swid": OTHER_SWID}).status_code == 200


# --- the pure parts ---

@pytest.mark.parametrize("raw,want", [
    (SWID, SWID), ("%7BAAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE%7D", SWID), ("AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE", SWID),
    (" " + SWID + "\n", SWID), ("{not-a-guid}", None), ("", None)])
def test_normalize_swid(raw, want):
    assert espn_connect.normalize_swid(raw) == want


def test_check_s2_keeps_the_value_as_it_is():
    assert espn_connect.check_s2(" " + S2 + " ") == S2
    assert espn_connect.check_s2("x" * 99) is None
    assert espn_connect.check_s2("x" * 120 + "; SWID=1") is None


def test_parse_entries_keeps_this_seasons_football():
    def pref(game, season, league, team):
        return {"typeId": 9, "metaData": {"entry": {"gameId": game, "seasonId": season, "entryId": team,
                                                    "groups": [{"groupId": league, "groupName": "L", "groupSize": 10}]}}}
    doc = {"preferences": [pref(1, 2026, 100000001, 7), pref(1, 2025, 1, 1), pref(2, 2026, 2, 2),
                           {"typeId": 1, "metaData": {}}, pref(1, 2026, 950458410, 1)]}
    got = espn_connect.parse_entries(doc, 2026)
    assert [(e.league_id, e.team_id) for e in got] == [("100000001", 7), ("950458410", 1)]


@pytest.mark.parametrize("ua,label", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 Version/26.6.1 Mobile Safari/604.1",
     "Safari on iPhone"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/156.0.0.0 Safari/537.36",
     "Chrome on Mac"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/26.5.2 Safari/605.1.15",
     "Safari on Mac"),
    ("", "Browser extension")])
def test_device_label(ua, label):
    assert espn_connect.device_label(ua) == label
