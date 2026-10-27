"""ffl-soc.4.12: no implicit league anywhere. Every function on league data takes the league it works on; nothing
falls back to a home league, FFL_LEAGUE or the first one registered. The CLI names its league through
`leagues.acting()` (FFL_USER on FFL_LEAGUE, else that user's default); what belongs to no league (an NFL-wide read
through a league's view, the app's own notices) says so through `leagues.oldest()`."""
import dataclasses
import re
from pathlib import Path

import sqlalchemy as sa

from ffl import config, leagues

FFL = Path(__file__).resolve().parent.parent / "ffl"
FORBIDDEN = {
    r"ref: (leagues\.)?LeagueRef \| None = None": "a ref that defaults to no league",
    r"\bref=None\b": "a ref that defaults (or is passed as) no league",
    r"\bleagues\.home\(": "the home league",
    r"\bleagues\.current\(": "the current league",
    r"\b(ref|r|lr)\.home\b": "a ref's home flag",
    r"\bconfig\.(LEAGUE_ID|MY_TEAM_ID|DRAFT_ROOM_URL|LEAGUE_URL)\b": "config's home league",
}


def test_no_implicit_league_in_the_code():
    hits = []
    for path in sorted(FFL.rglob("*.py")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for pattern, what in FORBIDDEN.items():
                if re.search(pattern, line):
                    hits.append(f"{path.relative_to(FFL.parent)}:{n}: {what}: {line.strip()}")
    assert hits == [], "\n".join(hits)


def test_the_registry_has_no_home_league():
    assert not hasattr(leagues, "home") and not hasattr(leagues, "current")
    assert "home" not in {f.name for f in dataclasses.fields(leagues.LeagueRef)}
    assert not any(hasattr(config, n) for n in ("LEAGUE_ID", "MY_TEAM_ID", "DRAFT_ROOM_URL", "LEAGUE_URL"))


def _at_0028(empty_db, monkeypatch, with_user: bool):
    """A database as production stands before 0029: the home league (team 7) and the public one (team 1), home's
    switches in the global prefs row beside the quiet hours, the public league's in its own state."""
    from alembic import command
    from ffl import db
    url = empty_db()
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0027")
    eng = sa.create_engine(url)                          # not db.engine(): it refuses a schema short of head
    with eng.begin() as c:
        if with_user:
            c.execute(sa.text("INSERT INTO users (email, created_at) VALUES ('me@example.com', 'now')"))
        c.execute(sa.text("INSERT INTO leagues (key, provider, provider_id, season, name, team_id, home, added_at) "
                          "VALUES ('espn:950458410', 'espn', '950458410', 2026, 'Public', 1, false, '9999')"))
        c.execute(sa.text("INSERT INTO notify_state (season, key, value) VALUES (2026, 'prefs', CAST(:v AS JSON))"),
                  {"v": '{"move": false, "news": true, "quiet": ["22:00", "06:00"]}'})
        c.execute(sa.text("INSERT INTO league_state (league, season, key, value) "
                          "VALUES ('espn:950458410', 2026, 'notify_prefs', CAST(:v AS JSON))"), {"v": '{"injury": false}'})
    eng.dispose()
    command.upgrade(db._alembic_config(), "head")
    db.reset()


def test_0029_moves_home_switches_to_its_own_state_and_back(empty_db, monkeypatch):
    from alembic import command
    from ffl import db, notify
    _at_0028(empty_db, monkeypatch, with_user=True)
    home, public = leagues.get("espn:100000001"), leagues.get("espn:950458410")
    assert (home.team_id, public.team_id) == (7, 1), "0028 put each team on the owner's membership"
    assert db.get_state("prefs") == {"quiet": ["22:00", "06:00"]}, "the global row keeps only the quiet hours"
    assert notify.prefs(ref=home)["move"] is False and notify.prefs(ref=home)["news"] is True
    assert notify.prefs(ref=public)["injury"] is False and notify.prefs(ref=public)["move"] is True
    assert notify.quiet() == ["22:00", "06:00"]
    command.downgrade(db._alembic_config(), "0028")
    with db._read() as c:
        rows = c.execute(sa.text("SELECT key, team_id, home FROM leagues ORDER BY key")).all()
        prefs = c.execute(sa.text("SELECT value FROM notify_state WHERE key = 'prefs'")).scalar()
        theirs = c.execute(sa.text("SELECT league FROM league_state WHERE key = 'notify_prefs'")).scalars().all()
    assert [tuple(r) for r in rows] == [("espn:100000001", 7, True), ("espn:950458410", 1, False)]
    assert prefs == {"move": False, "news": True, "quiet": ["22:00", "06:00"]} and theirs == ["espn:950458410"]


def test_0029_without_a_user_still_takes_home_switches_out_of_the_global_row(empty_db, monkeypatch):
    """No user (a fresh or mock database): nobody holds a team, and home's switches still move to its own state."""
    from ffl import db
    _at_0028(empty_db, monkeypatch, with_user=False)
    home = leagues.get("espn:100000001")
    assert home.team_id is None and db.get_league_state("notify_prefs", ref=home) == {"move": False, "news": True}
