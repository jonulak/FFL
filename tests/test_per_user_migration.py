"""0030 (ffl-soc.6.2.3): the personal rows production holds come through under its one user (the lowest id) and the
team that user holds, unchanged; the downgrade puts them back exactly, dropping only what a second user made since."""
import json

import pytest
import sqlalchemy as sa

from ffl import db, leagues, notify

HOME = "espn:100000001"
TABLES = {  # table: the columns 0029 has, in a stable order
    "decisions": "id, league, season, at, week, kind, source, title, decision",
    "watchlist": "league, season, player_id, added_at",
    "watch_signals": "league, season, key, player_id, kind, text, at",
    "sits": "league, season, week, player_id, at",
    "notices": "league, season, key, kind, title, body, url, created_at, sent_at, outcome",
    "push_subscriptions": "endpoint, keys::text, ua, created_at",
    "claim_plans": "league, season, week, plan::text, updated",
    "seats": "league, season, player_id, seat_id",
    "notify_state": "season, key, value::text",
    "league_state": "league, season, key, value::text",
    "league_caps": "league, pos, provider_limit, my_cap",
}


def _at_0029(empty_db, monkeypatch, with_user: bool = True) -> str:
    """Production as it stands before 0030: one user holding home (team 7), and a row in every personal table."""
    from alembic import command
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0027")
    eng = sa.create_engine(url)
    if with_user:
        with eng.begin() as c:
            c.execute(sa.text("INSERT INTO users (email, created_at) VALUES ('me@example.com', 'now')"))
    command.upgrade(db._alembic_config(), "0029")
    j = json.dumps
    with eng.begin() as c:
        x = lambda sql, **kw: c.execute(sa.text(sql), kw)
        x("INSERT INTO decisions (league, season, at, week, kind, source, title, decision) "
          "VALUES (:l, 2026, '2026-10-20', 7, 'note', 'app', 'Mine', 'kept')", l=HOME)
        x("INSERT INTO watchlist VALUES (:l, 2026, 11, '2026-10-20')", l=HOME)
        x("INSERT INTO watch_signals VALUES (:l, 2026, 'usage:11', 11, 'usage', 'up', '2026-10-21')", l=HOME)
        x("INSERT INTO sits VALUES (:l, 2026, 7, 12, '2026-10-20')", l=HOME)
        x("INSERT INTO notices (league, season, key, kind, title, body, url, created_at) "
          "VALUES (:l, 2026, 'move:1', 'move', 't', 'b', '/transactions', '2026-10-20')", l=HOME)
        x("INSERT INTO push_subscriptions VALUES ('https://push/me', CAST(:k AS JSON), 'iPhone', '2026-10-01')",
          k=j({"p256dh": "k", "auth": "a"}))
        x("INSERT INTO claim_plans VALUES (:l, 2026, 7, CAST(:p AS JSON), '2026-10-20')", l=HOME, p=j({"week": 7}))
        x("INSERT INTO seats VALUES (:l, 2026, 13, 14)", l=HOME)
        for key, v in (("prefs", {"quiet": ["22:00", "06:00"]}), ("news_mutes", [15]),
                       ("player_presets", {"presets": [], "default": "waivers"}), ("source_status", {"cbs": {}})):
            x("INSERT INTO notify_state VALUES (2026, :k, CAST(:v AS JSON))", k=key, v=j(v))
        for key, v in (("notify_prefs", {"move": False}), ("decision_prefs", {"log": False}),
                       ("watch_state", {"last_pass": "2026-10-20"}), ("pending_claims", [{"id": "c1", "add": 16}]),
                       ("waiver_run", "2026-10-22T03:00"), ("espn_week", 8)):
            x("INSERT INTO league_state VALUES (:l, 2026, :k, CAST(:v AS JSON))", l=HOME, k=key, v=j(v))
        x("INSERT INTO league_caps VALUES (:l, 'QB', 4, 3) ON CONFLICT (league, pos) DO UPDATE SET my_cap = 3", l=HOME)
    eng.dispose()
    return url


def _snapshot(url: str) -> dict[str, list]:
    eng = sa.create_engine(url)
    with eng.connect() as c:
        out = {t: sorted(tuple(r) for r in c.execute(sa.text(f"SELECT {cols} FROM {t}"))) for t, cols in TABLES.items()}
    eng.dispose()
    return out


def test_0030_files_everything_under_the_one_user_and_back(empty_db, monkeypatch):
    from alembic import command
    url = _at_0029(empty_db, monkeypatch)
    before = _snapshot(url)
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    home = leagues.get(HOME)
    assert home.user_id is not None and home.team_id == 7
    assert [d["title"] for d in db.decisions(ref=home)] == ["Mine"]
    assert db.watched_ids(ref=home) == [11] and [s["key"] for s in db.watch_signals("2000", ref=home)] == ["usage:11"]
    assert db.sat_ids(7, ref=home) == {12} and [n["key"] for n in db.unsent_notices(ref=home)] == ["move:1"]
    assert [s["endpoint"] for s in db.subscriptions(user_id=home.user_id)] == ["https://push/me"]
    assert db.claim_plan(7, ref=home) == {"week": 7} and db.seat_map(ref=home) == {13: 14}
    assert notify.quiet(home.user_id) == ["22:00", "06:00"]
    assert db.get_state("news_mutes", user_id=home.user_id) == [15]
    assert db.get_state("player_presets", user_id=home.user_id) == {"presets": [], "default": "waivers"}
    assert db.get_state("source_status") == {"cbs": {}}, "a global key stays global"
    assert notify.prefs(ref=home)["move"] is False
    assert db.get_league_state("decision_prefs", ref=home) == {"log": False}
    assert db.get_league_state("watch_state", ref=home) == {"last_pass": "2026-10-20"}
    assert db.get_league_state("pending_claims", ref=home) == [{"id": "c1", "add": 16}]
    assert db.get_league_state("waiver_run", ref=home) == "2026-10-22T03:00"
    assert db.get_league_state("espn_week", ref=home) == 8, "a league key stays the league's"
    assert leagues.caps(home)["QB"] == 3
    # a second user's rows come and go with the downgrade; the first user's go back exactly as they were
    b = db.create_user("b@example.com", None)
    leagues.join(HOME, "b@example.com", team_id=3)
    theirs = leagues.membership(b, HOME)
    db.put_watch(99, True, ref=theirs)
    db.put_claim_plan({"week": 7, "theirs": True}, ref=theirs)
    db.put_subscription("https://push/b", {"p256dh": "k", "auth": "b"}, user_id=b)
    db.put_my_cap("QB", 2, ref=theirs)
    db.put_league_state("notify_prefs", {"move": True}, ref=theirs)
    db.reset()
    command.downgrade(db._alembic_config(), "0029")
    assert _snapshot(url) == before


def test_0030_refuses_personal_rows_with_no_user(empty_db, monkeypatch):
    from alembic import command
    _at_0029(empty_db, monkeypatch, with_user=False)
    with pytest.raises(RuntimeError, match="no user"):
        command.upgrade(db._alembic_config(), "head")
