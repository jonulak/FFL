"""ffl-soc.6.2.3: one user never sees another's data. A and B are members of the home league on different teams, and B
holds a league A is not in (tests/two_users.py). Each seeds their own rows, marked with a sentinel no real row carries;
every route and personal db reader, as the other, must show none of it, and each must still see their own (without
that, a reader that answers nothing to anyone would pass)."""
from ffl import espn_auth
import pytest

from ffl import api, db, leagues, notify, player_views
from tests import two_users
from tests.two_users import act_as

A_MARK, B_MARK = "A-SENTINEL", "B-SENTINEL"
A_PID, B_PID = 987654321, 987654322     # a watched, sat, seated, muted player no roster holds
WEEK = 5


@pytest.fixture
def two():
    return two_users.make()


@pytest.fixture
def client():
    return two_users.client()


@pytest.fixture(autouse=True)
def _no_news_reads(monkeypatch):
    """/news/player reads owners and the player index from ESPN: neither is what these tests are about."""
    def fail(*a, **k):
        raise RuntimeError("no ESPN in this test")
    monkeypatch.setattr(api.pipeline, "owners", fail)
    monkeypatch.setattr(api.news_players, "fetch_index", fail)


def _seed(client, monkeypatch, ref, uid: int, mark: str, pid: int) -> None:
    """Everything personal `uid` keeps, in league `ref` and globally: through the routes where they are cheap, through
    the db where the route reads ESPN first."""
    act_as(monkeypatch, uid)
    lg = {"league": ref.key}
    assert client.post(f"/watch/{pid}", params=lg, json={"watched": True}).status_code == 200
    assert client.post("/notify/subscribe", json={"endpoint": f"https://push.example/{mark}",
                                                   "keys": {"p256dh": "k", "auth": "a"}, "ua": mark}).status_code == 200
    quiet = ["21:00", "06:00"] if mark == A_MARK else ["22:30", "05:30"]
    assert client.post("/notify/prefs", params=lg, json={"move": mark != A_MARK, "quiet": quiet}).status_code == 200
    preset = {**{k: v for k, v in player_views.SEED.items() if k != "id"}, "name": mark}
    assert client.post("/players/presets", params=lg, json=preset).status_code == 200
    assert client.post(f"/news/mute/{pid}", json={"muted": True}).status_code == 200
    assert client.post("/decisions/prefs", params=lg, json={"log": mark != A_MARK}).status_code == 200
    leagues.set_cap(ref, "K", 0 if mark == A_MARK else 1)   # the draft tools' caps: no route since ffl-srn, still per member
    db.add_decision({"at": "2026-10-27T12:00:00-04:00", "week": WEEK, "kind": "note", "source": "app", "title": mark,
                     "decision": mark}, ref=ref)
    db.put_sit(WEEK, pid, True, ref=ref)
    db.add_notices([{"key": f"injury:{WEEK}:{pid}:OUT", "kind": "injury", "title": mark, "body": mark, "url": "/roster",
                     "created_at": notify.now()}], ref=ref)
    db.add_watch_signals([{"key": f"usage:{pid}:{mark}", "player_id": pid, "kind": "usage", "text": mark,
                           "at": "2026-10-27T12:00:00-04:00"}], ref=ref)


# --- routes ---

def _route_texts(client, monkeypatch, uid: int, ref) -> dict[str, str]:
    """What `uid` reads, route by route, in league `ref`; each must answer 200 (a 404 would hide a leak)."""
    act_as(monkeypatch, uid)
    lg = {"league": ref.key}
    out = {}
    for path, params in (("/watch/ids", lg), ("/decisions", lg), ("/decisions/prefs", lg), ("/notify/recent", lg),
                         ("/notify/config", lg), ("/players/presets", lg),
                         (f"/news/player/{A_PID}", lg), (f"/news/player/{B_PID}", lg)):
        r = client.get(path, params=params)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        out[path] = r.text
    return out


def test_no_route_shows_one_member_the_others_rows(two, client, monkeypatch):
    _seed(client, monkeypatch, two.a, two.a_id, A_MARK, A_PID)
    _seed(client, monkeypatch, two.b, two.b_id, B_MARK, B_PID)
    as_b, as_a = _route_texts(client, monkeypatch, two.b_id, two.b), _route_texts(client, monkeypatch, two.a_id, two.a)
    for path in as_b:
        assert A_MARK not in as_b[path] and B_MARK not in as_a[path], path
        if not path.startswith("/news/player/"):  # that page names the player asked about; its mute is checked below
            assert str(A_PID) not in as_b[path] and str(B_PID) not in as_a[path], path
    # each sees their own
    for texts, mark, pid in ((as_a, A_MARK, A_PID), (as_b, B_MARK, B_PID)):
        assert str(pid) in texts["/watch/ids"] and mark in texts["/decisions"] and mark in texts["/notify/recent"]
        assert mark in texts["/players/presets"]


def test_settings_are_each_members_own(two, client, monkeypatch):
    _seed(client, monkeypatch, two.a, two.a_id, A_MARK, A_PID)
    _seed(client, monkeypatch, two.b, two.b_id, B_MARK, B_PID)
    for uid, ref, quiet, move, log, k_cap, devices in ((two.a_id, two.a, ["21:00", "06:00"], False, False, 0, 1),
                                                      (two.b_id, two.b, ["22:30", "05:30"], True, True, 1, 1)):
        act_as(monkeypatch, uid)
        lg = {"league": ref.key}
        cfg = client.get("/notify/config", params=lg).json()
        assert (cfg["prefs"]["quiet"], cfg["prefs"]["move"], cfg["devices"]) == (quiet, move, devices), uid
        assert client.get("/decisions/prefs", params=lg).json()["log"] is log, uid
        assert db.caps_rows(ref=ref)["K"]["my_cap"] == k_cap, uid
        assert client.get(f"/news/player/{A_PID}", params=lg).json()["muted"] is (uid == two.a_id), uid
        assert client.get(f"/news/player/{B_PID}", params=lg).json()["muted"] is (uid == two.b_id), uid


def test_one_member_cannot_change_the_others_rows(two, client, monkeypatch):
    """A write naming the other member's row answers as if it did not exist, and leaves it as it was."""
    _seed(client, monkeypatch, two.a, two.a_id, A_MARK, A_PID)
    act_as(monkeypatch, two.a_id)
    lg = {"league": two.a.key}
    [mine] = [d for d in client.get("/decisions", params=lg).json()["decisions"] if d["title"] == A_MARK]
    [preset] = [p for p in client.get("/players/presets", params=lg).json()["presets"] if p["name"] == A_MARK]
    act_as(monkeypatch, two.b_id)
    lg = {"league": two.b.key}
    assert client.post(f"/decisions/{mine['id']}", params=lg, json={"outcome": B_MARK}).status_code == 404
    assert client.post(f"/players/presets/{preset['id']}", params=lg, json={"name": B_MARK}).status_code == 404
    assert client.post(f"/players/presets/{preset['id']}/delete", params=lg).status_code == 404
    assert db.decision(mine["id"], ref=two.a)["outcome"] is None
    assert [p["name"] for p in db.get_state("player_presets", user_id=two.a_id)["presets"]][-1] == A_MARK


def test_a_league_the_other_is_not_in_answers_as_unknown(two, client, monkeypatch):
    act_as(monkeypatch, two.a_id)
    r = client.get("/watch/ids", params={"league": two.b_only.key})
    assert r.status_code == 404 and r.json()["detail"]["error"] == "unknown_league"


# --- db readers ---

def test_no_personal_db_reader_shows_one_member_the_others_rows(two, client, monkeypatch):
    _seed(client, monkeypatch, two.a, two.a_id, A_MARK, A_PID)
    for ref in (two.b, two.b_only):
        assert A_PID not in db.watched_ids(ref=ref)
        assert not [s for s in db.watch_signals("2000", ref=ref) if A_MARK in s["text"]]
        assert A_PID not in db.sat_ids(WEEK, ref=ref)
        assert not [d for d in db.decisions(ref=ref) if d["title"] == A_MARK]
        assert not [n for n in db.recent_notices(ref=ref) if n["title"] == A_MARK]
        assert not [n for n in db.unsent_notices(ref=ref) if n["title"] == A_MARK]
    assert leagues.caps(two.b).get("K") != 0
    # the co-manager is a user of their own: none of A's personal rows either
    assert A_PID not in db.watched_ids(ref=two.c) and A_PID not in db.sat_ids(WEEK, ref=two.c)
    assert not [d for d in db.decisions(ref=two.c) if d["title"] == A_MARK]


def test_what_espn_keeps_per_team_is_the_teams(two):
    """The claim plan and seats are the team's, as on ESPN: A's co-manager shares them, B (another team) does not."""
    db.put_claim_plan({"week": WEEK, "groups": [], "mark": A_MARK}, ref=two.a)
    db.put_seats({A_PID: A_PID + 1}, ref=two.a)
    assert (db.claim_plan(WEEK, ref=two.c) or {}).get("mark") == A_MARK
    assert db.seat_map(ref=two.c) == {A_PID: A_PID + 1}
    assert db.claim_plan(WEEK, ref=two.b) is None
    assert db.seat_map(ref=two.b) == {}


# --- pushes ---

def _devices(client, monkeypatch, two) -> None:
    for uid, mark in ((two.a_id, A_MARK), (two.b_id, B_MARK)):
        act_as(monkeypatch, uid)
        assert client.post("/notify/subscribe", json={"endpoint": f"https://push.example/{mark}",
                                                       "keys": {"p256dh": "k", "auth": "a"}}).status_code == 200


def _flush() -> list[tuple[str, dict]]:
    from datetime import datetime
    got: list[tuple[str, dict]] = []
    notify.flush(datetime.fromisoformat("2026-10-27T12:00:00-04:00"), send=lambda sub, p: got.append((sub["endpoint"], p)))
    return got


def test_a_members_notice_reaches_only_their_devices(two, client, monkeypatch):
    _devices(client, monkeypatch, two)
    db.add_notices([{"key": f"injury:{WEEK}:{A_PID}:OUT", "kind": "injury", "title": A_MARK, "body": A_MARK,
                     "url": "/roster", "created_at": notify.now()}], ref=two.a)
    got = _flush()
    assert [ep for ep, _ in got] == [f"https://push.example/{A_MARK}"]


def test_a_league_move_reaches_every_member_once(two, client, monkeypatch):
    """A move in the league is news to each member: each one's devices get it once, on that member's own switches."""
    _devices(client, monkeypatch, two)
    notify.enqueue_moves([{"id": "2026-10-27T10:00:00|3|FA ADDED|1", "action": "FA ADDED", "team": "Bears",
                           "player": "Someone"}], ref=two.a)
    got = sorted(ep for ep, _ in _flush())
    assert got == [f"https://push.example/{A_MARK}", f"https://push.example/{B_MARK}"]


def test_a_starter_out_is_news_to_each_member_whose_starter_he_is(two, monkeypatch):
    """One roster read for the league; each member hears of their own team's starters (a co-manager too)."""
    from ffl import espn_season

    def entry(pid, name):
        return {"playerId": pid, "lineupSlotId": 2,
                "playerPoolEntry": {"player": {"injuryStatus": "OUT", "fullName": name}}}
    doc = {"scoringPeriodId": WEEK, "teams": [{"id": 7, "roster": {"entries": [entry(9, "Mine")]}},
                                             {"id": 3, "roster": {"entries": [entry(19, "Theirs")]}}]}
    reads = []
    monkeypatch.setattr(espn_season, "raw_league_get", lambda view, **k: reads.append(view) or doc)
    monkeypatch.setattr(espn_season, "fetch_injuries", lambda: {9: {"status": "Out"}, 19: {"status": "Out"}})
    starters = [{"teamId": t, "rosterForCurrentScoringPeriod": {"entries": [{"playerId": p, "lineupSlotId": 2}]}}
                for t, p in ((7, 9), (3, 19))]
    db.put_box_doc(WEEK, {"matchups": [{"home": starters[0], "away": starters[1]}]}, "h", 0.0, ref=two.a)
    assert notify.check_injuries() == 3
    assert reads == ["mRoster"], "one read for every member"
    keys = lambda ref: [n["key"] for n in db.unsent_notices(ref=ref)]
    assert keys(two.a) == keys(two.c) == [f"injury:{WEEK}:9:OUT"] and keys(two.b) == [f"injury:{WEEK}:19:OUT"]


def test_a_waiver_run_is_reported_to_each_member_of_the_team_that_claimed(two, monkeypatch):
    """Waivers are the team's (ESPN keeps claims per team): the run is read once, each team's pending claims are its
    own, and each member on a team hears of that team's result."""
    from types import SimpleNamespace
    from ffl import espn_season, espn_write
    monkeypatch.setattr(espn_season, "fresh_league", lambda *a, **k: SimpleNamespace(
        current_week=WEEK, player_map={11: "Ray Davis", 12: "Tyler Allgeier"}))
    runs = iter(["2026-10-01T03:10:00", "2026-10-08T03:12:00"])
    monkeypatch.setattr(espn_write, "last_waiver_run", lambda **k: next(runs))
    pending = {7: [{"id": "a", "add": 11, "drop": None, "priority": 1}], 3: [{"id": "b", "add": 12, "drop": None,
                                                                             "priority": 1}]}
    monkeypatch.setattr(espn_write, "pending_claims", lambda **k: pending[k["ref"].team_id])
    assert notify.check_waivers(ref=leagues.get(two.a.key)) == 0     # first sight: each team's baseline
    assert db.get_league_state("pending_claims", ref=two.c) == pending[7], "the co-manager shares the team's"
    assert db.get_league_state("pending_claims", ref=two.b) == pending[3]
    pending = {7: [], 3: []}
    db.add_transactions([{"id": "1791000000000-7-WAIVER ADDED-11", "date": "2026-10-08T03:12", "type": "WAIVER ADDED",
                          "team": "A", "player": "Ray Davis", "action": "WAIVER ADDED", "bid": 0, "reasoning": ""}],
                        ref=two.a)
    assert notify.check_waivers(ref=leagues.get(two.a.key)) == 3
    body = lambda ref: [n["body"] for n in db.unsent_notices(ref=ref) if n["kind"] == "waivers"]
    assert body(two.a) == body(two.c) == ["Won Ray Davis."] and body(two.b) == ["Lost Tyler Allgeier."]


def test_each_member_gets_their_own_watch_pass(two, monkeypatch):
    seen = []
    monkeypatch.setattr(api, "watch_pass", lambda *, ref: seen.append((ref.user_id, ref.team_id)) or {"queued": 0})
    out = api.watch_members(ref=leagues.get(two.a.key))
    assert sorted(seen) == sorted([(two.a_id, 7), (two.b_id, 3), (two.c.user_id, 7)])
    assert set(out) == {str(two.a_id), str(two.b_id), str(two.c.user_id)}


def test_app_notices_go_to_the_admins_alone(two, monkeypatch):
    """Notices about the app itself (a source failing) reach the admins, under their default league; nobody else,
    nobody when there is no admin, and not an admin with no league."""
    row = {"key": "source:cbs:2026-10-27", "kind": "source", "title": A_MARK, "body": "b", "url": "/players",
           "created_at": notify.now()}
    assert notify.app_notices([row]) == 0
    db.set_admin(two.a_id, True)
    db.set_admin(db.create_user("leagueless@example.com", None), True)
    assert notify.app_notices([row]) == 1
    titles = lambda ref: [n["title"] for n in db.unsent_notices(ref=ref)]
    assert titles(two.a) == [A_MARK] and titles(two.b) == titles(two.b_only) == titles(two.c) == []


def test_each_member_is_their_own_team_whoever_the_env_cookie_is(two, monkeypatch):
    """The ESPN keys in the environment decide nothing: B's pages are built for B's team, never the cookie's."""
    from ffl import config, espn_season
    espn_auth.connect(two.a_id, "s2", "{A}")
    monkeypatch.setattr(espn_season, "load", lambda *a, **k: {"teams": [{"id": 7, "owner_swids": ["{A}"]},
                                                                        {"id": 3, "owner_swids": ["{B}"]}]})
    assert [espn_season.my_team_id(None, ref=r) for r in (two.a, two.b, two.c, two.b_only)] == [7, 3, 7, 4]
