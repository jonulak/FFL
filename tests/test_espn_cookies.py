"""ffl-soc.3: every user's ESPN cookies are their own row, sealed; a write goes out under the acting user's cookies only,
a shared league read under any member's that still work."""
import pytest
import sqlalchemy as sa

from ffl import config, db, espn_auth, espn_write, leagues
from tests import two_users

S2_A, SWID_A = "s2-of-a-" + "x" * 40, "{AAAAAAAA-0000}"
S2_B, SWID_B = "s2-of-b-" + "y" * 40, "{BBBBBBBB-0000}"


@pytest.fixture
def two():
    t = two_users.make()
    espn_auth.connect(t.a_id, S2_A, SWID_A)
    espn_auth.connect(t.b_id, S2_B, SWID_B)
    return t


def _raw(sql: str) -> list[str]:
    with db.engine().connect() as c:
        return [r[0] for r in c.execute(sa.text(sql))]


def test_the_stored_row_carries_no_cookie(two):
    """What a pg_dump would carry: the column holds neither cookie, nor any part of them."""
    raw = " ".join(_raw("SELECT secret FROM espn_credentials"))
    for secret in (S2_A, SWID_A, S2_B, SWID_B, "AAAAAAAA", "s2-of"):
        assert secret not in raw
    assert espn_auth.own(two.a_id).jar() == {"espn_s2": S2_A, "SWID": SWID_A}


def test_the_stored_push_keys_are_sealed(two):
    db.put_subscription("https://push/a", {"p256dh": "p256-secret", "auth": "auth-secret"}, user_id=two.a_id)
    raw = " ".join(_raw("SELECT keys FROM push_subscriptions"))
    assert "secret" not in raw and "p256dh" not in raw
    assert db.subscriptions(user_id=two.a_id)[0]["keys"] == {"p256dh": "p256-secret", "auth": "auth-secret"}


def test_a_row_under_another_ring_reads_as_not_connected(two, monkeypatch):
    """A worktree database restored from production's dump: production's ring is not here."""
    from ffl import vault
    monkeypatch.setenv("FFL_SECRET_KEYS", vault.keygen())
    assert espn_auth.own(two.a_id) is None
    assert espn_auth.status(two.a_id)["connected"] is False


def test_cookies_never_show_in_a_repr(two):
    c = espn_auth.own(two.a_id)
    assert S2_A not in repr(c) and SWID_A not in repr(c)


def test_a_read_takes_the_members_own_cookies_first(two):
    assert espn_auth.for_read(two.a).user_id == two.a_id
    assert espn_auth.for_read(two.b).user_id == two.b_id


def test_a_shared_read_falls_back_to_another_members_working_cookies(two):
    espn_auth.disconnect(two.b_id)
    assert espn_auth.for_read(two.b).user_id == two.a_id
    espn_auth.refused(espn_auth.own(two.a_id), "ESPN 401")
    assert espn_auth.for_read(two.b) is None, "failing cookies are nobody's fallback"


def test_refused_cookies_come_back_with_new_ones(two):
    espn_auth.refused(espn_auth.own(two.a_id), "ESPN 401")
    assert espn_auth.own(two.a_id) is None and espn_auth.status(two.a_id)["failing_since"]
    espn_auth.connect(two.a_id, S2_A, SWID_A)
    assert espn_auth.own(two.a_id) is not None and espn_auth.status(two.a_id)["failing_since"] is None


def test_a_league_is_built_with_its_members_cookies(two, monkeypatch):
    built = []

    class FakeLeague:
        def __init__(self, league_id, year, espn_s2=None, swid=None, fetch_league=True):
            built.append((league_id, espn_s2, swid))

    monkeypatch.setattr("espn_api.football.League", FakeLeague)
    config.league.cache_clear()
    config.league(two.b_only)
    assert built == [(999, S2_B, SWID_B)], "B's own league, B's cookies"


def test_a_refused_build_marks_those_cookies_and_tries_another_member(two, monkeypatch):
    from espn_api.requests.espn_requests import ESPNAccessDenied
    built = []

    class FakeLeague:
        def __init__(self, league_id, year, espn_s2=None, swid=None, fetch_league=True):
            built.append(espn_s2)
            if espn_s2 == S2_B:
                raise ESPNAccessDenied("League cannot be accessed with the provided credentials")

    monkeypatch.setattr("espn_api.football.League", FakeLeague)
    config.league.cache_clear()
    config.league(two.b)
    assert built == [S2_B, S2_A]
    assert espn_auth.status(two.b_id)["failing_since"] is not None


def test_a_refused_read_on_a_built_league_marks_its_cookies_and_reads_again(two, monkeypatch):
    """Cookies that worked at the build and are refused an hour later: the read goes again under another member's."""
    from espn_api.requests.espn_requests import ESPNAccessDenied
    reads = []

    class Req:
        def __init__(self, s2):
            self.s2 = s2

        def league_get(self, params=None, headers=None, extend=""):
            reads.append(self.s2)
            if self.s2 == S2_B:
                raise ESPNAccessDenied("League cannot be accessed with the provided credentials")
            return {"ok": True}

    class FakeLeague:
        def __init__(self, league_id, year, espn_s2=None, swid=None, fetch_league=True):
            self.espn_request = Req(espn_s2)

    monkeypatch.setattr("espn_api.football.League", FakeLeague)
    config.league.cache_clear()
    assert config.raw_league_get("mRoster", ref=two.b) == {"ok": True}
    assert reads == [S2_B, S2_A] and espn_auth.status(two.b_id)["failing_since"] is not None


def test_a_private_league_nobody_can_read_says_so(two, monkeypatch):
    from espn_api.requests.espn_requests import ESPNAccessDenied

    class FakeLeague:
        def __init__(self, *a, espn_s2=None, **k):
            raise ESPNAccessDenied("espn_s2 and swid are required")

    for uid in (two.a_id, two.b_id):
        espn_auth.disconnect(uid)
    monkeypatch.setattr("espn_api.football.League", FakeLeague)
    config.league.cache_clear()
    with pytest.raises(espn_auth.NoConnection, match="working ESPN connection"):
        config.league(two.a)


class _Posted:
    def __init__(self, monkeypatch, status=200, answer=None):
        self.calls = []
        monkeypatch.setenv("FFL_WRITES", "1")
        monkeypatch.setattr(espn_write, "_team_id", lambda *, ref: ref.team_id)

        def post(url, json=None, headers=None, cookies=None, timeout=None):
            self.calls.append({"url": url, "body": json, "cookies": cookies})

            class R:
                status_code = status
                text = "refused"

                @staticmethod
                def json():
                    return answer or {"id": "c1", "status": "EXECUTED"}
            return R()

        monkeypatch.setattr(espn_write.requests, "post", post)


def test_each_users_write_carries_their_own_cookies_and_swid(two, monkeypatch):
    sent = _Posted(monkeypatch)
    espn_write.submit_claim(11, None, 8, ref=two.a)
    espn_write.submit_claim(12, None, 8, ref=two.b)
    (a, b) = sent.calls
    assert a["cookies"] == {"espn_s2": S2_A, "SWID": SWID_A} and a["body"]["memberId"] == SWID_A
    assert b["cookies"] == {"espn_s2": S2_B, "SWID": SWID_B} and b["body"]["memberId"] == SWID_B
    assert a["body"]["teamId"] == 7 and b["body"]["teamId"] == 3


def test_a_write_never_borrows_a_league_mates_cookies(two, monkeypatch):
    sent = _Posted(monkeypatch)
    espn_auth.disconnect(two.b_id)
    with pytest.raises(espn_write.NotConnected):
        espn_write.move_lineup([(1, "WR", "BE")], 8, ref=two.b)
    assert sent.calls == []


def test_a_write_on_refused_cookies_is_refused_with_a_plain_reason(two, monkeypatch):
    sent = _Posted(monkeypatch)
    espn_auth.refused(espn_auth.own(two.a_id), "ESPN 401")
    with pytest.raises(espn_write.WriteError) as e:
        espn_write.add_free_agent(11, None, 8, ref=two.a)
    assert sent.calls == [] and "reconnect" in e.value.message.lower()


def test_espn_refusing_a_write_marks_the_cookies(two, monkeypatch):
    _Posted(monkeypatch, status=401)
    with pytest.raises(espn_write.WriteError) as e:
        espn_write.submit_claim(11, None, 8, ref=two.a)
    assert e.value.expired and "reconnect" in e.value.message.lower()
    assert espn_auth.status(two.a_id)["failing_since"] is not None


def test_pending_claims_are_read_with_the_teams_own_cookies(two, monkeypatch):
    """ESPN shows a team's pending claims to that team alone (its co-managers included, as on ESPN): a co-manager's
    cookies serve, a league-mate's never, nor a league-mate's kept read."""
    monkeypatch.setenv("FFL_WRITES", "1")
    espn_write.reset_fakes()
    seen = []

    def get(view, extend="", headers=None, *, ref, cookies=None, **params):
        seen.append(cookies.user_id if cookies else None)
        return {"pendingTransactions": [], "scoringPeriodId": 8}

    monkeypatch.setattr(config, "raw_league_get", get)
    monkeypatch.setattr(espn_write, "_team_id", lambda *, ref: ref.team_id)
    espn_write.pending_state(60, ref=two.c)                  # C has none: A's, the same team's
    espn_write.pending_state(60, ref=two.a)                  # the team's kept read
    espn_write.pending_state(60, ref=two.b)
    assert seen == [two.a_id, two.b_id]
    espn_auth.disconnect(two.a_id)
    with pytest.raises(espn_write.NotConnected):
        espn_write.pending_state(ref=two.c)                  # B's cookies are another team's: never lent
    with pytest.raises(espn_write.NotConnected):
        espn_write.move_lineup([(1, "WR", "BE")], 8, ref=two.c)


def test_a_co_managers_write_never_borrows_the_teammates_cookies(two, monkeypatch):
    sent = _Posted(monkeypatch)
    with pytest.raises(espn_write.NotConnected):
        espn_write.submit_claim(11, None, 8, ref=two.c)
    assert sent.calls == []


def test_adding_a_league_finds_the_team_by_the_users_own_swid(two, monkeypatch):
    monkeypatch.setattr(config, "raw_league_get", lambda *a, ref, **k: {
        "settings": {"name": "New"}, "teams": [{"id": 2, "owners": [SWID_A]}, {"id": 5, "owners": [SWID_B]}]})
    monkeypatch.setattr("ffl.settings.collect", lambda ref: None)
    assert leagues.add("12345", 2026, user_id=two.b_id).team_id == 5


def test_the_api_will_not_start_without_a_ring(monkeypatch):
    from fastapi.testclient import TestClient

    from ffl import api, vault
    monkeypatch.delenv("FFL_SECRET_KEYS")
    with pytest.raises(vault.VaultError, match="FFL_SECRET_KEYS"):
        with TestClient(api.app):
            pass


def test_deleting_the_account_drops_the_cookies(two):
    db.delete_user(two.b_id)
    assert _raw(f"SELECT user_id FROM espn_credentials WHERE user_id = {two.b_id}") == []


def test_a_refused_read_never_repoints_the_shared_league_endpoint(monkeypatch):
    """espn-api's 401 handling set the shared LEAGUE_ENDPOINT to leagueHistory while it retried, so another thread's
    read on the same League got a 404 ("League … does not exist") (#489). A 401 now raises, the endpoint untouched."""
    from types import SimpleNamespace

    from espn_api.requests import espn_requests as er
    asked = []
    monkeypatch.setattr(er.requests, "get", lambda url, **k: asked.append(url) or SimpleNamespace(status_code=401))
    req = er.EspnFantasyRequests("nfl", 2026, 950458410, cookies={"espn_s2": "x", "SWID": "{y}"})
    before = req.LEAGUE_ENDPOINT
    with pytest.raises(er.ESPNAccessDenied, match="cannot be accessed"):
        req.league_get(params={"view": "mRoster"})
    assert req.LEAGUE_ENDPOINT == before and asked == [before]
    with pytest.raises(er.ESPNAccessDenied, match="espn_s2 and swid are required"):
        er.EspnFantasyRequests("nfl", 2026, 950458410).league_get()
