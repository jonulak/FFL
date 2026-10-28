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


# What the API and its jobs run (ffl-soc.6.2.3): they act for the signed-in user or for each member in turn, never as
# the CLI's user (FFL_USER, leagues.acting) nor through the first league registered.
SERVER = ["api.py", "notify.py", "espn_season.py", "espn_write.py", "live.py", "outlook.py", "watch.py", "season.py",
          "auth.py", "claims.py", "news.py", "nfl.py", "plays.py", "playbook.py", "journal.py", "newsdesk"]
SERVER_FORBIDDEN = {
    r"\bleagues\.acting(_user)?\(": "the CLI's user",
    r"FFL_USER\b": "the CLI's user",
    r"\bleagues\.oldest\(": "the first league registered",
}
OLDEST_OK = {"news.py", "newsdesk/players.py"}  # NFL-wide player reads through some league's view: no one's data


def test_no_implicit_user_on_the_server():
    hits = []
    for name in SERVER:
        for path in sorted((FFL / name).rglob("*.py")) if (FFL / name).is_dir() else [FFL / name]:
            rel = str(path.relative_to(FFL))
            cli = False  # a module's command line (def main, if __name__ == "__main__") acts as FFL_USER, as the CLI does
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.match(r"(def |class |if __name__)", line):
                    cli = line.startswith(("def main(", "if __name__"))
                if cli:
                    continue
                for pattern, what in SERVER_FORBIDDEN.items():
                    if re.search(pattern, line) and not (what.startswith("the first") and rel in OLDEST_OK):
                        hits.append(f"ffl/{rel}:{n}: {what}: {line.strip()}")
    assert hits == [], "\n".join(hits)


def test_the_registry_has_no_home_league():
    assert not hasattr(leagues, "home") and not hasattr(leagues, "current")
    assert "home" not in {f.name for f in dataclasses.fields(leagues.LeagueRef)}
    assert not any(hasattr(config, n) for n in ("LEAGUE_ID", "MY_TEAM_ID", "DRAFT_ROOM_URL", "LEAGUE_URL"))


def _at_0028(empty_db, monkeypatch, with_user: bool) -> str:
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
    command.upgrade(db._alembic_config(), "0029")
    return url


def _q(url: str, sql: str) -> list[tuple]:
    """Rows straight off the database at the revision a test left it (db's readers are head's)."""
    eng = sa.create_engine(url)
    with eng.connect() as c:
        rows = [tuple(r) for r in c.execute(sa.text(sql))]
    eng.dispose()
    return rows


def test_0029_moves_home_switches_to_its_own_state_and_back(empty_db, monkeypatch):
    from alembic import command
    from ffl import db
    url = _at_0028(empty_db, monkeypatch, with_user=True)
    assert _q(url, "SELECT league, team_id FROM memberships ORDER BY league") == \
        [("espn:100000001", 7), ("espn:950458410", 1)], "0028 put each team on the owner's membership"
    assert _q(url, "SELECT value FROM notify_state WHERE key = 'prefs'") == [({"quiet": ["22:00", "06:00"]},)], \
        "the global row keeps only the quiet hours"
    assert _q(url, "SELECT league, value FROM league_state WHERE key = 'notify_prefs' ORDER BY league") == \
        [("espn:100000001", {"move": False, "news": True}), ("espn:950458410", {"injury": False})]
    assert not {"home", "team_id"} & {c for (c,) in _q(url, "SELECT column_name FROM information_schema.columns "
                                                           "WHERE table_name = 'leagues'")}
    command.downgrade(db._alembic_config(), "0028")
    assert _q(url, "SELECT key, team_id, home FROM leagues ORDER BY key") == \
        [("espn:100000001", 7, True), ("espn:950458410", 1, False)]
    assert _q(url, "SELECT value FROM notify_state WHERE key = 'prefs'") == \
        [({"move": False, "news": True, "quiet": ["22:00", "06:00"]},)]
    assert _q(url, "SELECT league FROM league_state WHERE key = 'notify_prefs'") == [("espn:950458410",)]


def test_0029_without_a_user_still_takes_home_switches_out_of_the_global_row(empty_db, monkeypatch):
    """No user (a fresh or mock database): nobody holds a team, and home's switches still move to its own state."""
    url = _at_0028(empty_db, monkeypatch, with_user=False)
    assert _q(url, "SELECT count(*) FROM memberships") == [(0,)]
    assert _q(url, "SELECT value FROM league_state WHERE league = 'espn:100000001' AND key = 'notify_prefs'") == \
        [({"move": False, "news": True},)]
