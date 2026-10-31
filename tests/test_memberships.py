"""ffl-soc.6.2.2 part A: memberships. A league is opened through the signed-in user's membership (a non-member's
answer is an unknown league's), the user's default when none is named; pages are cached per (user, league)."""
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from ffl import api, auth, db, leagues
from tests.conftest import TEST_USER
from tests.home import home_league

OTHER = leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, None, None)


@pytest.fixture
def other():
    return leagues.register(OTHER, user_id=TEST_USER["id"])


@pytest.fixture
def client():
    return TestClient(api.app)


def _as(monkeypatch, uid: int):
    """Every request passes as user `uid` (a row in users)."""
    user = db.user_row(uid)
    monkeypatch.setattr(auth, "decide", lambda *a, **k: (0, user))


# --- the registry ---

def test_the_test_user_holds_home():
    home = home_league()
    assert home.user_id == TEST_USER["id"] and home.is_default and home.owner
    assert leagues.for_user(TEST_USER["id"]) == [home]


def test_register_files_the_league_under_the_user_named(other):
    assert other.user_id == TEST_USER["id"] and other.team_id == 3 and not other.is_default and other.owner
    assert [r.key for r in leagues.for_user(TEST_USER["id"])] == [home_league().key, "espn:999"]


def test_join_and_membership(other):
    uid = db.create_user("b@example.com", None)
    with pytest.raises(SystemExit):
        leagues.membership(uid, "espn:999")
    assert leagues.for_user(uid) == []
    leagues.join("espn:999", "B@example.com", team_id=5, default=True)
    ref = leagues.membership(uid, "espn:999")
    assert (ref.user_id, ref.team_id, ref.is_default, ref.owner) == (uid, 5, True, False)
    assert leagues.default_for(uid) == ref
    assert leagues.get("espn:999").user_id == TEST_USER["id"]  # the owner's view is untouched
    with pytest.raises(SystemExit):
        leagues.join("espn:999", "nobody@example.com", team_id=5)
    with pytest.raises(SystemExit):
        leagues.join("espn:404", "b@example.com", team_id=5)


def test_one_default_per_user(other):
    leagues.join("espn:999", TEST_USER["email"], team_id=3, default=True)
    assert [r.key for r in leagues.for_user(TEST_USER["id"]) if r.is_default] == ["espn:999"]
    assert leagues.default_for(TEST_USER["id"]).key == "espn:999"


def test_rename_is_the_members_own(other):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5)
    leagues.rename(leagues.membership(uid, "espn:999"), "  Mine   now ")
    assert leagues.membership(uid, "espn:999").name == "Mine now"
    assert leagues.get("espn:999").name == "other"
    assert db.league_rows()[1]["name"] == "other"


def test_a_rename_stays_off_the_league_row(other):
    leagues.rename(other, "Renamed")
    leagues.register(leagues.get("espn:999"), user_id=TEST_USER["id"])  # formats.store and add write it back so
    with db._read() as c:
        assert c.execute(sa.select(db.leagues_table.c.name).where(db.leagues_table.c.key == "espn:999")).scalar() == "other"
    assert leagues.get("espn:999").name == "Renamed"


# --- the API ---

def test_open_league_is_the_users_membership(client, other, monkeypatch):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5, default=True)
    _as(monkeypatch, uid)
    seen = []
    monkeypatch.setattr(api.espn_season, "roster_state", lambda week=None, **k: seen.append(k["ref"]) or {"week": 3, "cards": []})
    monkeypatch.setattr(api.outlook, "attach", lambda cards, *a, **k: cards)
    monkeypatch.setattr(api.outlook, "load", lambda *a, **k: {})
    assert client.get("/roster").status_code == 200
    assert (seen[-1].key, seen[-1].user_id, seen[-1].team_id) == ("espn:999", uid, 5)


def _league_routes():
    """Every GET route that opens a league (Depends(open_league)) and takes no path parameter."""
    out = []
    for r in api.app.routes:
        dep = getattr(r, "dependant", None)
        if not dep or "GET" not in getattr(r, "methods", ()) or "{" in r.path:
            continue
        if any(d.call is api.open_league for d in dep.dependencies):
            out.append(r.path)
    return out


def test_a_non_member_is_answered_as_an_unknown_league(client, other, monkeypatch):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5, default=True)
    _as(monkeypatch, uid)
    home = home_league().key
    paths = _league_routes()
    assert len(paths) > 10
    for p in paths:
        a = client.get(p, params={"league": home})
        b = client.get(p, params={"league": "espn:404"})
        assert a.status_code == 404 and a.json()["detail"]["error"] == "unknown_league", p
        assert b.status_code == 404
        assert {**a.json()["detail"], "league": None} == {**b.json()["detail"], "league": None}, p


def test_a_user_with_no_league(client, monkeypatch):
    _as(monkeypatch, db.create_user("b@example.com", None))
    r = client.get("/roster")
    assert r.status_code == 404 and r.json()["detail"]["error"] == "no_leagues"
    assert client.get("/leagues").json() == []


def test_leagues_lists_only_the_users(client, other, monkeypatch):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5, default=True)
    _as(monkeypatch, uid)
    rows = client.get("/leagues").json()
    assert [(r["key"], r["home"]) for r in rows] == [("espn:999", True)]


def test_rename_route_renames_only_the_users(client, other, monkeypatch):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5, default=True)
    _as(monkeypatch, uid)
    assert client.post("/leagues/name", params={"league": "espn:999"}, json={"name": "B's"}).json()["name"] == "B's"
    assert leagues.get("espn:999").name == "other"


# --- page caches ---

def test_caches_are_per_user(other):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=3)
    mine, theirs = leagues.get("espn:999"), leagues.membership(uid, "espn:999")
    assert api.cache_for(mine) is not api.cache_for(theirs)
    assert api.cache_for(mine) is api.cache_for(leagues.get("espn:999"))
    assert api.cache_for(home_league()) is api.cache_for(home_league())


def test_a_second_member_of_home_does_not_get_homes_cache():
    uid = db.create_user("b@example.com", None)
    leagues.join(home_league().key, "b@example.com", team_id=4)
    assert api.cache_for(leagues.membership(uid, home_league().key)) is not api.cache_for(home_league())


def test_stored_pages_are_per_user(other):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=3)
    mine, theirs = leagues.get("espn:999"), leagues.membership(uid, "espn:999")
    db.put_page("roster", {"who": "mine"}, 1.0, ref=mine)
    db.put_page("roster", {"who": "theirs"}, 1.0, ref=theirs)
    assert [p["payload"] for p in db.pages(ref=mine)] == [{"who": "mine"}]
    assert [p["payload"] for p in db.pages(ref=theirs)] == [{"who": "theirs"}]


def test_a_new_move_drops_every_members_pages(other, monkeypatch):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=3)
    caches = [api.cache_for(leagues.get("espn:999")), api.cache_for(leagues.membership(uid, "espn:999"))]
    for c in caches:
        c.get_or_build("transactions", lambda: {"rows": []}, 60)
    monkeypatch.setattr(api, "_start_rewarm", lambda c, dropped: None)
    monkeypatch.setattr(api.espn_season, "forget_current_boxes", lambda **k: None)
    api.on_new_rows([{"id": "x"}], ref=leagues.get("espn:999"))
    assert [c.has("transactions") for c in caches] == [False, False]


def test_a_move_drops_rosters_and_cards_only_of_its_teams_and_players(other, monkeypatch):
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5)  # the owner is on team 3, the one that moved
    owner, member = api.cache_for(leagues.get("espn:999")), api.cache_for(leagues.membership(uid, "espn:999"))
    keys = ("roster", "team:3", "team:5", "player:42", "player:7", "week:current", "waivers")
    for c in (owner, member):
        for k in keys:
            c.get_or_build(k, lambda: {}, 60)
    monkeypatch.setattr(api, "_start_rewarm", lambda c, dropped: None)
    monkeypatch.setattr(api.espn_season, "forget_current_boxes", lambda **k: None)
    api.on_new_rows([{"id": "1761800000000-3-WAIVER ADDED-42", "player_id": 42}], ref=leagues.get("espn:999"))
    kept = {c: [k for k in keys if c.has(k)] for c in (owner, member)}
    assert kept == {owner: ["team:5", "player:7"], member: ["roster", "team:5", "player:7"]}
    api.on_new_rows([], ref=leagues.get("espn:999"))  # a rollover drops every move page
    assert not any(member.has(k) for k in ("team:5", "player:7"))


# --- the migration ---

def _at_0027(empty_db, monkeypatch, with_user: bool):
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0027")
    eng = sa.create_engine(url)                          # not db.engine(): it refuses a schema short of head
    with eng.begin() as c:
        c.execute(sa.text("UPDATE leagues SET name = 'Renamed', strategy = '2qb-10team'"))
        c.execute(sa.text("INSERT INTO page_cache (league, season, key, payload, built_at) "
                          "VALUES ('espn:100000001', 2026, 'roster', '{}', 1.0)"))
        if with_user:
            c.execute(sa.text("INSERT INTO users (email, created_at) VALUES ('me@example.com', 'now')"))
            c.execute(sa.text("INSERT INTO users (email, created_at) VALUES ('later@example.com', 'now')"))
    eng.dispose()
    command.upgrade(db._alembic_config(), "0028")
    return url


def _q(url: str, sql: str) -> list[tuple]:
    """Rows as 0028 left them (db's readers are head's)."""
    eng = sa.create_engine(url)
    with eng.connect() as c:
        rows = [tuple(r) for r in c.execute(sa.text(sql))]
    eng.dispose()
    return rows


def test_migration_files_every_league_under_the_first_user(empty_db, monkeypatch):
    url = _at_0027(empty_db, monkeypatch, with_user=True)
    [(me,)] = _q(url, "SELECT id FROM users WHERE email = 'me@example.com'")
    assert _q(url, "SELECT user_id, league, team_id, is_default, name, strategy FROM memberships") == [
        (me, "espn:100000001", 7, True, None, "2qb-10team")]
    assert _q(url, "SELECT user_id FROM page_cache") == [(me,)]
    assert _q(url, "SELECT name FROM leagues") == [("Renamed",)], "the league's name stays the league's"


def test_migration_without_a_user_files_nothing(empty_db, monkeypatch):
    url = _at_0027(empty_db, monkeypatch, with_user=False)
    assert _q(url, "SELECT count(*) FROM memberships") == [(0,)]
    assert _q(url, "SELECT user_id FROM page_cache") == [(0,)]  # no user's: served as before


def test_sign_in_never_makes_a_membership(monkeypatch):
    monkeypatch.setenv("FFL_SIGNUP_EMAILS", "new@example.com")
    uid = auth.sign_in("google", "g9", "new@example.com", True, None)
    assert leagues.for_user(uid) == []


def test_join_from_the_command_line(other, capsys):
    uid = db.create_user("b@example.com", None)
    assert leagues.main(["join", "espn:999", "--user", "b@example.com", "--team", "5", "--default"]) == 0
    ref = leagues.membership(uid, "espn:999")
    assert (ref.team_id, ref.is_default) == (5, True)


def test_a_member_never_writes_to_espn_with_the_owners_cookies(other, monkeypatch):
    """ffl-soc.3: a write goes out under the member's own cookies; one with none is refused, the owner's never lent."""
    from ffl import espn_auth, espn_write
    monkeypatch.setenv("FFL_WRITES", "1")
    monkeypatch.setattr(espn_write, "_team_id", lambda *, ref: ref.team_id)
    sent = []
    from types import SimpleNamespace
    monkeypatch.setattr(espn_write.requests, "post",
                        lambda *a, **k: sent.append(k["cookies"]) or SimpleNamespace(status_code=200, json=lambda: {}))
    espn_auth.connect(0, "owners-s2", "{OWNER}")
    uid = db.create_user("b@example.com", None)
    leagues.join("espn:999", "b@example.com", team_id=5)
    with pytest.raises(espn_write.NotConnected) as e:
        espn_write.cancel_claim("c1", 8, ref=leagues.membership(uid, "espn:999"))
    assert not e.value.expired and "own ESPN connection" in e.value.message and sent == []
    espn_write.cancel_claim("c1", 8, ref=leagues.get("espn:999"))  # the owner, under the owner's own
    assert sent == [{"espn_s2": "owners-s2", "SWID": "{OWNER}"}]


# Routes that open no league: global (health, the switcher's list, live, sources, news, devices, traces, presets, a
# player's outlook, shared by every league) or a job that walks every league or resolves its own ?league= through
# open_league (outlook refresh, transactions sync, refresh). A new route that is
# neither must open the league (Depends(open_league)) or be added here on purpose: a league route without it would
# serve the owner's league to any signed-in user.
GLOBAL_ROUTES = {
    "/health", "/leagues", "/live", "/live/status", "/sources/status", "/decisions/presets", "/outlook/refresh",
    "/outlook/{player_id:int}", "/refresh",
    "/transactions/sync", "/notify/subscribe", "/notify/unsubscribe", "/notify/test", "/trace",
    "/notify/tick", "/news/run", "/news/search", "/news/mute/{player_id}", "/news/status", "/llm/usage",
}


def test_every_route_opens_the_league_or_is_global_on_purpose():
    def calls(dep):
        for d in dep.dependencies:
            yield d.call
            yield from calls(d)
    bare = {r.path for r in api.app.routes
            if getattr(r, "dependant", None) and api.open_league not in set(calls(r.dependant))}
    assert {p for p in bare if not p.startswith("/auth/")} == GLOBAL_ROUTES


def test_there_is_no_module_page_cache():
    """Every page cache is a member's of one league (cache_for): a module-level one would serve one member's pages to
    every member."""
    assert not hasattr(api, "cache")
