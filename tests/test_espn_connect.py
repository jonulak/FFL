"""Connecting ESPN (ffl-soc.6.3.3): the extension's pairing, the cookies it sends, the manual paste, status, devices
and disconnect, through the real middleware (`real_auth`): three routes are public and must hold by their own secret.
Then the user's leagues (ffl-soc.6.3.4): listed off their own login, added or joined.
ESPN is never reached: the fan API and the league reads are stubbed."""
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ffl import api, auth, config, db, espn_auth, espn_connect, leagues, settings

pytestmark = pytest.mark.real_auth

SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}"
OTHER_SWID = "{11111111-2222-3333-4444-555555555555}"
S2 = "AEB" + "x" * 150 + "%2B" + "y" * 100 + "%3D"   # URL-encoded, as the cookie holds it


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("FFL_JOB_TOKEN", "job-token-for-tests")
    for w in espn_connect._limits():
        w.clear()
    espn_connect._REFUSALS.clear()


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
    (row,) = db.credential_rows(uid)
    assert row["espn_s2"] == S2 and row["swid"] == SWID   # espn_s2 never decoded; SWID normalised
    assert ("100000001", {"espn_s2": S2, "SWID": SWID}) in espn["calls"]
    st = app.get("/espn/status").json()
    assert st["connected"] is True and st["devices"] == 1 and "espn_s2" not in st and "swid" not in st


def test_a_stale_swid_beside_the_logins_is_passed_over(espn, monkeypatch):
    """ESPN's scripts write SWID too, so a browser can hold a stale one the cookies API hands back first (Jonathan
    2026-10-29: Chrome and Safari both refused until a sign-out and in). The extension sends the rest as `others`;
    the first ESPN knows is the login, and a refusal while trying them is not left on the status."""
    stale = "{11111111-2222-3333-4444-555555555555}"
    fan = espn_connect.fan_entries

    def known(swid):
        if swid == stale:
            raise espn_connect.ConnectError(422, "unknown_login", "unknown")
        return fan(swid)
    monkeypatch.setattr(espn_connect, "fan_entries", known)
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    auth_ = {"authorization": f"Bearer {_pair(app, ext)}"}
    r = ext.post("/espn/cookies", headers=auth_, json={"espn_s2": S2, "swid": stale, "others": [SWID, 7]})
    assert r.status_code == 200 and r.json()["refused"] is None
    (row,) = db.credential_rows(uid)
    assert row["swid"] == SWID


def test_a_refused_send_shows_on_the_status_until_one_connects(espn, monkeypatch):
    """The wizard waits on /espn/status for the login: a send ESPN refused says why there (code and message only)."""
    def unknown(swid):
        raise espn_connect.ConnectError(422, "unknown_login", "ESPN doesn't know this browser's login.")
    monkeypatch.setattr(espn_connect, "fan_entries", unknown)
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    auth_ = {"authorization": f"Bearer {_pair(app, ext)}"}
    r = ext.post("/espn/cookies", headers=auth_, json={"espn_s2": S2, "swid": SWID, "others": []})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "unknown_login"
    st = app.get("/espn/status").json()
    assert st["refused"]["error"] == "unknown_login" and S2 not in str(st) and "AAAAAAAA" not in str(st)
    monkeypatch.setattr(espn_connect, "fan_entries", lambda swid: espn["entries"])
    assert ext.post("/espn/cookies", headers=auth_, json={"espn_s2": S2, "swid": SWID}).status_code == 200
    assert app.get("/espn/status").json()["refused"] is None


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
    assert db.credential_rows(uid) == []


def test_espn_down_stores_nothing(espn):
    uid = db.create_user("me@example.com", None)
    espn["refuse"] = 500
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2, "swid": SWID})
    assert r.status_code == 502 and r.json()["detail"]["error"] == "espn_unavailable"
    assert db.credential_rows(uid) == []


def test_public_leagues_prove_nothing_and_the_login_is_stored_unproven(espn):
    uid = db.create_user("me@example.com", None)
    espn["entries"] = [espn_connect.Entry(str(i), 1, config.SEASON, "L", 10) for i in range(40)]
    espn["public"] = {str(i) for i in range(40)}
    r = _signed_in(uid).post("/espn/connect", json={"espn_s2": S2, "swid": SWID})
    assert r.status_code == 200 and r.json()["proven"] is False
    assert db.credential_rows(uid)[0]["swid"] == SWID
    assert len([c for c in espn["calls"] if c[0] != "fan"]) == espn_connect.CHECK_LEAGUES   # never all 40


def test_no_leagues_or_a_bad_swid_is_refused(espn):
    app = _signed_in(db.create_user("me@example.com", None))
    espn["entries"] = []
    assert app.post("/espn/connect", json={"espn_s2": S2, "swid": SWID}).json()["detail"]["error"] == "no_leagues"
    r = app.post("/espn/connect", json={"espn_s2": S2, "swid": "AAAAAAAA-anon"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "bad_swid"
    assert app.post("/espn/connect", json={"espn_s2": "short", "swid": SWID}).json()["detail"]["error"] == "bad_espn_s2"


def test_a_second_espn_account_is_kept_beside_the_first(espn):
    """#460: another SWID is another account, not a replacement; the same SWID however copied is the same one."""
    uid = db.create_user("me@example.com", None)
    app = _signed_in(uid)
    first = app.post("/espn/connect", json={"espn_s2": S2, "swid": SWID}).json()["account"]
    second = app.post("/espn/connect", json={"espn_s2": S2 + "z", "swid": OTHER_SWID}).json()
    assert second["account"] != first and len(second["accounts"]) == 2
    assert [(r["swid"], r["espn_s2"]) for r in db.credential_rows(uid)] == [(SWID, S2), (OTHER_SWID, S2 + "z")]
    # the first account in another case or encoding is an update of it
    again = app.post("/espn/connect", json={"espn_s2": S2 + "y", "swid": "%7B" + SWID[1:-1].lower() + "%7D"}).json()
    assert again["account"] == first
    assert [r["espn_s2"] for r in db.credential_rows(uid)] == [S2 + "y", S2 + "z"]


def test_disconnecting_one_account_takes_its_leagues_and_keeps_the_rest(espn):
    """#460 (Jonathan 2026-10-28): a login removed takes the user's place in its leagues with it; a league left on
    would read on through a league-mate as if another member used the app."""
    uid = db.create_user("me@example.com", None)
    app, ext = _signed_in(uid), _client()
    _pair(app, ext)
    app.post("/espn/connect", json={"espn_s2": S2, "swid": SWID})
    other = app.post("/espn/connect", json={"espn_s2": S2, "swid": OTHER_SWID}).json()["account"]
    db.put_membership(uid, "espn:100000001", team_id=3)                                    # names none: the first's
    leagues.register(leagues.LeagueRef("espn:777", "espn", "777", config.SEASON, "Other", None, None, None),
                     user_id=None)
    db.put_membership(uid, "espn:777", team_id=4, espn_account=db.swid_hash(OTHER_SWID))
    assert app.post("/espn/disconnect", json={"account": other}).json() == {"ok": True, "left": ["espn:777"]}
    assert [r["swid"] for r in db.credential_rows(uid)] == [SWID]
    assert [r.key for r in leagues.for_user(uid)] == ["espn:100000001"]
    assert leagues._raw("espn:777") is not None, "the league stays registered"
    assert len(app.get("/espn/devices").json()["devices"]) == 1
    assert app.post("/espn/disconnect", json={"account": other}).status_code == 404
    # another user's account id is not theirs to disconnect
    them = db.create_user("them@example.com", None)
    espn_auth.connect(them, S2, OTHER_SWID)
    theirs = db.credential_rows(them)[0]["id"]
    assert app.post("/espn/disconnect", json={"account": theirs}).status_code == 404
    assert len(db.credential_rows(them)) == 1
    assert app.post("/espn/disconnect", json={"account": "x"}).status_code == 400


def test_new_cookies_clear_a_failing_mark(espn):
    uid = db.create_user("me@example.com", None)
    espn_auth.connect(uid, S2, SWID)
    espn_auth.refused(espn_auth.own(uid), "ESPN refused a shared read")
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
    db.put_membership(uid, "espn:100000001", team_id=3)
    assert app.post("/espn/disconnect").json() == {"ok": True, "left": ["espn:100000001"]}
    assert db.credential_rows(uid) == [] and app.get("/espn/devices").json() == {"devices": []}
    assert leagues.for_user(uid) == []
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


# --- the user's leagues (ffl-soc.6.3.4) ---

NEW_RAW = {"settings": {"name": "Work League", "size": 12,
                        "scoringSettings": {"scoringItems": [{"statId": 53, "points": 1}]},
                        "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 4},
                        "rosterSettings": {"lineupSlotCounts": {"0": 1, "20": 2}, "positionLimits": {"1": 3}},
                        "draftSettings": {}, "acquisitionSettings": {}},
           "status": {"firstScoringPeriod": 1},
           "teams": [{"id": 2, "owners": ["{X}"]}, {"id": 5, "owners": [SWID]}]}


class _Resp:
    def __init__(self, status, doc=None):
        self.status_code, self._doc = status, doc

    def json(self):
        return self._doc


@pytest.fixture
def mine(monkeypatch):
    """A signed-in user with their own cookies stored, and ESPN as a stub: the fan API lists `entries`, a league read
    answers `docs[league]` (or `status[league]`), and a new league's settings read (settings.collect) answers NEW_RAW
    unless its id is in `collect_fails`. `calls` counts the fan API and league reads."""
    uid = db.create_user("me@example.com", "Me")
    espn_auth.connect(uid, S2, SWID)
    state = {"uid": uid, "calls": {"fan": 0, "league": 0}, "status": {}, "collect_fails": set(), "jars": [],
             "other_entries": [], "collected": [],
             "entries": [espn_connect.Entry("999", 5, config.SEASON, "Work", 12),
                         espn_connect.Entry("100000001", 3, config.SEASON, "Home", 10)],
             "docs": {"999": {"settings": {"name": "Work League", "size": 12},
                              "teams": [{"id": 5, "name": "Mine", "logo": "https://x/l.png",
                                         "owners": [SWID.lower()]}]},
                      "100000001": {"settings": {"name": "Home League", "size": 10},
                                    "teams": [{"id": 3, "location": "Big", "nickname": "Dogs", "owners": [SWID]}]}}}

    def fan(swid):
        assert swid in {r["swid"] for r in db.credential_rows(uid)}, "only ever the user's own stored SWIDs"
        state["calls"]["fan"] += 1
        return state["entries"] if swid == SWID else state["other_entries"]

    def get(url, params=None, cookies=None, timeout=None):
        assert cookies in [{"espn_s2": r["espn_s2"], "SWID": r["swid"]} for r in db.credential_rows(uid)], \
            "the user's own cookies, never a league-mate's"
        state["calls"]["league"] += 1
        state["jars"].append(cookies["SWID"])
        lid = url.rsplit("/", 1)[1]
        return _Resp(state["status"].get((lid, cookies["SWID"]), state["status"].get(lid, 200)), state["docs"].get(lid))

    def raw(view, **kw):
        state["collected"].append((kw["ref"].provider_id, kw["ref"].espn_account))
        if kw["ref"].provider_id in state["collect_fails"]:
            raise RuntimeError("ESPN fell over")
        return NEW_RAW

    monkeypatch.setattr(espn_connect, "fan_entries", fan)
    monkeypatch.setattr("requests.get", get)
    monkeypatch.setattr(config, "raw_league_get", raw)
    monkeypatch.setattr(settings, "raw_league_get", raw)
    espn_connect._CHOICES.clear()
    return state


def test_leagues_need_a_connection_and_a_session(mine):
    other = db.create_user("other@example.com", "Other")
    r = _signed_in(other).get("/espn/leagues")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_connected"
    assert _client().get("/espn/leagues").status_code == 401
    assert _client().post("/espn/leagues", json={"ids": ["999"]}).status_code == 401
    assert mine["calls"] == {"fan": 0, "league": 0}


def test_leagues_lists_the_users_own_with_their_team(mine):
    r = _signed_in(mine["uid"]).get("/espn/leagues")
    assert r.status_code == 200
    body = r.json()
    assert body["season"] == config.SEASON
    assert [(c["key"], c["name"], c["size"], c["team_id"], c["team"], c["logo"], c["ok"], c["joined"])
            for c in body["leagues"]] == [
        ("espn:100000001", "Home League", 10, 3, "Big Dogs", None, True, False),
        ("espn:999", "Work League", 12, 5, "Mine", "https://x/l.png", True, False)]
    # kept a few minutes: a second open asks ESPN nothing
    _signed_in(mine["uid"]).get("/espn/leagues")
    assert mine["calls"] == {"fan": 1, "league": 2}


def test_a_team_the_user_does_not_manage_cannot_be_picked(mine):
    mine["docs"]["999"]["teams"][0]["owners"] = ["{SOMEONE-ELSE}"]
    c = {c["key"]: c for c in _signed_in(mine["uid"]).get("/espn/leagues").json()["leagues"]}["espn:999"]
    assert not c["ok"] and c["problem"] == espn_connect.problem("not_your_team")
    assert set(c["problem"]) == {"error", "title", "message", "fix"}   # the hover's line and the dialog's fix
    r = _signed_in(mine["uid"]).post("/espn/leagues", json={"ids": ["999"], "default": None}).json()
    assert r["leagues"] == [{"league_id": "999", "key": "espn:999", "name": "Work League", "ok": False,
                             "error": "not_your_team", "message": c["problem"]["message"]}]
    assert leagues.for_user(mine["uid"]) == [] and leagues._raw("espn:999") is None


def test_a_refused_login_still_lists_its_leagues_greyed_with_the_problem(mine):
    """The fan API lists a login's leagues off its SWID alone: refused, they stay on the list (name and size, no team
    read) with the problem, and can't be picked; the login is marked failing."""
    mine["status"]["999"] = 401
    body = _signed_in(mine["uid"]).get("/espn/leagues").json()
    assert [(c["key"], c["name"], c["team"], c["ok"], c["problem"]["error"]) for c in body["leagues"]] == [
        ("espn:100000001", "Home", None, False, "refused"), ("espn:999", "Work", None, False, "refused")]
    assert espn_auth.status(mine["uid"])["failing_since"] is not None
    # refused already: listed without reading a league under it again
    reads = mine["calls"]["league"]
    espn_connect._CHOICES.clear()
    assert all(not c["ok"] for c in _signed_in(mine["uid"]).get("/espn/leagues").json()["leagues"])
    assert mine["calls"]["league"] == reads
    r = _signed_in(mine["uid"]).post("/espn/leagues", json={"ids": ["999"], "default": None}).json()
    assert r["leagues"][0]["error"] == "refused" and leagues.for_user(mine["uid"]) == []


def test_espn_down_for_one_league_greys_that_league_alone(mine):
    mine["status"]["100000001"] = 500
    body = _signed_in(mine["uid"]).get("/espn/leagues").json()
    assert [(c["key"], c["ok"], (c["problem"] or {}).get("error")) for c in body["leagues"]] == [
        ("espn:100000001", False, "espn_unavailable"), ("espn:999", True, None)]
    assert espn_auth.status(mine["uid"])["failing_since"] is None


def test_the_fan_api_down_lists_nothing(mine, monkeypatch):
    def down(swid):
        raise espn_connect.ConnectError(502, "espn_unavailable", "ESPN didn't answer. Try again in a minute.")
    monkeypatch.setattr(espn_connect, "fan_entries", down)
    r = _signed_in(mine["uid"]).get("/espn/leagues")
    assert r.status_code == 502 and r.json()["detail"]["error"] == "espn_unavailable"


def test_picking_adds_a_new_league_and_joins_a_known_one(mine):
    app = _signed_in(mine["uid"])
    r = app.post("/espn/leagues", json={"ids": ["999", 100000001], "default": None})
    assert r.status_code == 200
    body = r.json()
    assert [x["ok"] for x in body["leagues"]] == [True, True]
    assert body["default"] == "espn:999", "a user with no league: the first pick is the default"
    new = leagues.membership(mine["uid"], "espn:999")
    assert (new.team_id, new.name, new.strategy, new.is_default) == (5, "Work League", None, True)
    assert settings.load(new)["scoring"][0]["points"] == 1
    home = leagues.membership(mine["uid"], "espn:100000001")
    assert home.team_id == 3 and not home.is_default
    assert leagues._raw("espn:100000001").strategy == "2qb-10team", "a joined league's row is untouched"
    assert leagues.membership(0, "espn:100000001").team_id == 7
    assert all(c["joined"] for c in app.get("/espn/leagues").json()["leagues"])
    # again: nothing new, and the default stays
    again = app.post("/espn/leagues", json={"ids": ["100000001"], "default": None}).json()
    assert again["leagues"][0]["ok"] and again["default"] == "espn:999"


def test_a_named_default_moves_it(mine):
    app = _signed_in(mine["uid"])
    app.post("/espn/leagues", json={"ids": ["999"], "default": None})
    body = app.post("/espn/leagues", json={"ids": ["100000001"], "default": "100000001"}).json()
    assert body["default"] == "espn:100000001" and not leagues.membership(mine["uid"], "espn:999").is_default


def test_a_league_outside_the_users_entries_answers_as_one_that_does_not_exist(mine):
    """The home league is registered (user 0 is in it), but ESPN doesn't list this user in it: the same answer as an
    id nobody has, and no membership."""
    mine["entries"] = [mine["entries"][0]]
    r = _signed_in(mine["uid"]).post("/espn/leagues", json={"ids": ["100000001", "123456"], "default": "123456"})
    a, b = r.json()["leagues"]
    assert {k: v for k, v in a.items() if k != "league_id"} == {k: v for k, v in b.items() if k != "league_id"}
    assert a["error"] == "unknown_league" and leagues.for_user(mine["uid"]) == []


def test_one_league_failing_to_set_up_stops_none_of_the_others(mine):
    mine["collect_fails"].add("999")
    body = _signed_in(mine["uid"]).post("/espn/leagues", json={"ids": ["999", "100000001"], "default": None}).json()
    assert [(x["key"], x["ok"], x.get("error")) for x in body["leagues"]] == [
        ("espn:999", False, "setup_failed"), ("espn:100000001", True, None)]
    assert leagues._raw("espn:999") is None, "a failed read leaves no league without settings behind"
    assert [r.key for r in leagues.for_user(mine["uid"])] == ["espn:100000001"]


@pytest.mark.parametrize("body", [{}, {"ids": []}, {"ids": "999"}, {"ids": [True]}, {"ids": ["999"], "default": "5"},
                                  {"ids": [str(i) for i in range(21)]}])
def test_bad_picks_are_refused(mine, body):
    r = _signed_in(mine["uid"]).post("/espn/leagues", json=body)
    assert r.status_code == 400
    assert leagues.for_user(mine["uid"]) == []


# --- several ESPN accounts (#460) ---

def _second_account(mine, entries):
    espn_auth.connect(mine["uid"], S2 + "b", OTHER_SWID)
    mine["other_entries"] = entries
    mine["docs"]["777"] = {"settings": {"name": "Other League", "size": 8},
                           "teams": [{"id": 4, "name": "B Team", "owners": [OTHER_SWID]}]}
    # the home league lists both accounts' teams (a co-manager): each account's own team is its owner
    mine["docs"]["100000001"]["teams"].append({"id": 9, "name": "B at home", "owners": [OTHER_SWID]})


def test_leagues_list_every_account_and_a_league_under_two_is_the_first_ones(mine):
    _second_account(mine, [espn_connect.Entry("777", 4, config.SEASON, "Other", 8),
                           espn_connect.Entry("100000001", 9, config.SEASON, "Home", 10)])
    body = _signed_in(mine["uid"]).get("/espn/leagues").json()
    first, second = (r["id"] for r in db.credential_rows(mine["uid"]))
    assert [(c["key"], c["team_id"], c["account"]) for c in body["leagues"]] == [
        ("espn:100000001", 3, first), ("espn:777", 4, second), ("espn:999", 5, first)]
    assert body["problems"] == [] and all("_account" not in c for c in body["leagues"])


def test_each_picked_league_is_filed_under_the_account_that_lists_it(mine):
    _second_account(mine, [espn_connect.Entry("777", 4, config.SEASON, "Other", 8)])
    r = _signed_in(mine["uid"]).post("/espn/leagues", json={"ids": ["999", "777"], "default": None}).json()
    assert [x["ok"] for x in r["leagues"]] == [True, True]
    a, b = db.swid_hash(SWID), db.swid_hash(OTHER_SWID)
    work, other = (leagues.membership(mine["uid"], k) for k in ("espn:999", "espn:777"))
    assert (work.espn_account, other.espn_account) == (a, b)
    assert ("777", b) in mine["collected"], "a new league's settings are read under the account that lists it"
    # every later call for a league goes out under its own account's cookies
    assert espn_auth.own_for(work).swid == SWID and espn_auth.own_for(other).swid == OTHER_SWID
    st = _signed_in(mine["uid"]).get("/espn/status").json()
    assert [acc["leagues"] for acc in st["accounts"]] == [["espn:999"], ["espn:777"]]


def test_one_refused_account_greys_its_leagues_and_leaves_the_others(mine):
    _second_account(mine, [espn_connect.Entry("777", 4, config.SEASON, "Other", 8)])
    mine["status"][("777", OTHER_SWID)] = 401
    body = _signed_in(mine["uid"]).get("/espn/leagues").json()
    first, second = (r["id"] for r in db.credential_rows(mine["uid"]))
    assert [(c["key"], c["account"], c["ok"]) for c in body["leagues"]] == [
        ("espn:100000001", first, True), ("espn:777", second, False), ("espn:999", first, True)]
    st = espn_auth.status(mine["uid"])
    assert [a["connected"] for a in st["accounts"]] == [True, False] and st["connected"] is True


def test_a_league_under_two_logins_goes_to_the_one_that_can_pick_it(mine):
    _second_account(mine, [espn_connect.Entry("100000001", 9, config.SEASON, "Home", 10)])
    mine["status"][("100000001", SWID)] = 500          # the first login can't read it just now; the second can
    c = {c["key"]: c for c in _signed_in(mine["uid"]).get("/espn/leagues").json()["leagues"]}["espn:100000001"]
    assert c["ok"] and c["account"] == db.credential_rows(mine["uid"])[1]["id"] and c["team_id"] == 9


def test_the_wizard_lists_one_login_at_a_time(mine):
    _second_account(mine, [espn_connect.Entry("777", 4, config.SEASON, "Other", 8)])
    first, second = (r["id"] for r in db.credential_rows(mine["uid"]))
    app = _signed_in(mine["uid"])
    assert [c["key"] for c in app.get(f"/espn/leagues?account={second}").json()["leagues"]] == ["espn:777"]
    assert [c["key"] for c in app.get(f"/espn/leagues?account={first}").json()["leagues"]] == [
        "espn:100000001", "espn:999"]
    them = db.create_user("them@example.com", None)
    espn_auth.connect(them, S2, OTHER_SWID)
    theirs = db.credential_rows(them)[0]["id"]
    assert app.get(f"/espn/leagues?account={theirs}").status_code == 404


def test_a_login_refused_in_the_wizard_is_its_error_not_a_greyed_list(mine):
    """Jonathan 2026-10-29: refused while being added, the login is the wizard's error and nothing is listed to add;
    refused later (an expired login), Settings' full list shows its leagues greyed."""
    _second_account(mine, [espn_connect.Entry("777", 4, config.SEASON, "Other", 8)])
    mine["status"][("777", OTHER_SWID)] = 401
    second = db.credential_rows(mine["uid"])[1]["id"]
    app = _signed_in(mine["uid"])
    r = app.get(f"/espn/leagues?account={second}")
    assert r.status_code == 422 and r.json()["detail"]["error"] == "refused"
    full = {c["key"]: c for c in app.get("/espn/leagues").json()["leagues"]}
    assert full["espn:777"]["problem"]["error"] == "refused"


def test_the_fan_api_failing_for_the_wizards_login_is_its_error(mine, monkeypatch):
    _second_account(mine, [])
    second = db.credential_rows(mine["uid"])[1]["id"]
    real = espn_connect.fan_entries

    def fan(swid):
        if swid == OTHER_SWID:
            raise espn_connect.ConnectError(502, "espn_unavailable", "ESPN didn't answer. Try again in a minute.")
        return real(swid)
    monkeypatch.setattr(espn_connect, "fan_entries", fan)
    r = _signed_in(mine["uid"]).get(f"/espn/leagues?account={second}")
    assert r.status_code == 502 and r.json()["detail"]["error"] == "espn_unavailable"


def test_a_row_from_before_0033_is_named_when_first_opened_and_serves_unnamed_memberships(mine):
    """0033 leaves an existing row's swid_hash NULL (no key ring in a migration) and every existing membership's
    espn_account NULL: the first open names the row, and a NULL membership is the first account's."""
    t = db.espn_credentials
    with db._write() as c:
        c.execute(sa.update(t).where(t.c.user_id == mine["uid"]).values(swid_hash=None))
    assert db.credential_rows(mine["uid"])[0]["swid_hash"] == db.swid_hash(SWID)
    db.put_membership(mine["uid"], "espn:100000001", team_id=3)
    home = leagues.membership(mine["uid"], "espn:100000001")
    assert home.espn_account is None and espn_auth.own_for(home).swid == SWID
    # connecting a second account later leaves it on the first
    espn_auth.connect(mine["uid"], S2 + "b", OTHER_SWID)
    assert espn_auth.own_for(home).swid == SWID
    # and the same account sent again updates its row rather than adding one
    espn_auth.connect(mine["uid"], S2 + "c", SWID)
    assert [r["espn_s2"] for r in db.credential_rows(mine["uid"])] == [S2 + "c", S2 + "b"]


def test_a_row_this_ring_cannot_open_gives_way_to_a_new_login(mine):
    """A test server's database restored from production holds rows sealed under the box's ring: unopenable here, so
    never named. Connecting replaces it, as before 0033, rather than standing behind it as a dead 'first account'
    that every unnamed membership would resolve to."""
    uid = mine["uid"]
    espn_auth.disconnect(uid)
    with db._write() as c:
        c.execute(sa.insert(db.espn_credentials).values(user_id=uid, secret="not-a-vault-token", updated_at="then"))
    assert db.credential_rows(uid)[0]["unreadable"]
    db.put_membership(uid, "espn:100000001", team_id=3)
    espn_auth.connect(uid, S2, SWID)
    assert [r["swid"] for r in db.credential_rows(uid)] == [SWID]
    assert espn_auth.own_for(leagues.membership(uid, "espn:100000001")).swid == SWID


def test_leaving_one_league_from_settings(mine):
    app = _signed_in(mine["uid"])
    app.post("/espn/leagues", json={"ids": ["999", "100000001"], "default": None})
    assert app.post("/espn/leave", json={"league": "espn:100000001"}).json() == {"ok": True}
    assert [r.key for r in leagues.for_user(mine["uid"])] == ["espn:999"]
    assert leagues.membership(0, "espn:100000001").team_id == 7, "another member's place is untouched"
    assert app.post("/espn/leave", json={"league": "espn:100000001"}).status_code == 404
    assert app.post("/espn/leave", json={}).status_code == 400
    listed = {c["key"]: c["joined"] for c in app.get("/espn/leagues").json()["leagues"]}
    assert listed == {"espn:100000001": False, "espn:999": True}


@pytest.mark.parametrize("origin", ["chrome-extension://abcdefghijklmnop", "safari-web-extension://1A2B-3C4D"])
def test_the_extension_origins_pass_a_preflight_and_nobody_else(origin):
    """The extension posts from its own origin with a bearer: its preflight is answered before the session check; a
    web page's origin gets no allow header, and credentials are never allowed."""
    pre = {"origin": origin, "access-control-request-method": "POST",
           "access-control-request-headers": "authorization, content-type"}
    r = _client().options("/espn/cookies", headers=pre)
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in r.headers
    r = _client().post("/espn/pair/start", headers={"origin": origin})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == origin
    evil = _client().options("/espn/cookies", headers={**pre, "origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_the_wizard_posts_one_league_at_a_time_on_the_pickers_read(mine):
    """The Set up step posts each league alone: as many posts as MAX_PICKS stay inside the limit, and a post within
    a minute of the picker's read reuses it rather than reading every league from ESPN again."""
    app = _signed_in(mine["uid"])
    app.get("/espn/leagues")
    reads = dict(mine["calls"])
    for _ in range(espn_connect.MAX_PICKS):
        r = app.post("/espn/leagues", json={"ids": ["999"], "default": None})
        assert r.status_code == 200 and r.json()["leagues"][0]["ok"]
    assert mine["calls"] == reads


def test_check_again_reads_espn_past_the_cache(mine):
    app = _signed_in(mine["uid"])
    app.get("/espn/leagues")
    app.get("/espn/leagues")
    assert mine["calls"]["fan"] == 1
    app.get("/espn/leagues?fresh=true")
    assert mine["calls"]["fan"] == 2
