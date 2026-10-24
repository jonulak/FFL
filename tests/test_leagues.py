"""The league registry: the home league's facts live in the `leagues` table, never in .env."""
from datetime import date

import pytest
import sqlalchemy as sa

from ffl import config, db, leagues
from ffl.leagues import LeagueRef


def _clear():
    with db._write() as c:
        c.execute(sa.delete(db.leagues_table))


def test_migration_seeds_home_from_env(monkeypatch, tmp_path):
    from alembic import command
    path = tmp_path / "fresh.db"
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{path}")
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0014")
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    assert leagues.home() == LeagueRef("espn:100000001", "espn", "100000001", 2026, "home", 7, True, None, None)
    assert len(leagues.all_leagues()) == 1


def test_migration_without_env_seeds_nothing(monkeypatch, tmp_path):
    from alembic import command
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{tmp_path / 'bare.db'}")
    monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    assert leagues.all_leagues() == []


def test_registry_wins_over_stale_env(monkeypatch):
    monkeypatch.setenv("LEAGUE_ID", "111")
    assert leagues.home().provider_id == "100000001"
    assert config.LEAGUE_ID == 100000001
    assert config.MY_TEAM_ID == 7
    assert config.DRAFT_ROOM_URL.endswith("leagueId=100000001")
    assert config.LEAGUE_URL.endswith("leagueId=100000001")


def test_no_home_league_says_how_to_add_one():
    _clear()
    with pytest.raises(SystemExit, match=r"no home league: run `uv run python -m ffl.leagues add espn <id> --home`"):
        leagues.home()


def test_unknown_key_exits_with_known_keys():
    with pytest.raises(SystemExit) as e:
        leagues.get("espn:1")
    assert "espn:100000001" in str(e.value)


def test_current_defaults_to_home(monkeypatch):
    monkeypatch.delenv("FFL_LEAGUE", raising=False)
    assert leagues.current() == leagues.home()


def test_current_reads_env_at_call_time(monkeypatch):
    other = leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "other", 2, False, None, None))
    monkeypatch.setenv("FFL_LEAGUE", "espn:5")
    assert leagues.current() == other
    monkeypatch.setenv("FFL_LEAGUE", "espn:100000001")
    assert leagues.current().provider_id == "100000001"


def test_register_upserts_and_a_new_home_demotes_the_old_one():
    leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "mock", 2, True, None, None))
    assert leagues.home().key == "espn:5"
    assert [r.key for r in leagues.all_leagues()] == ["espn:5", "espn:100000001"]
    leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "renamed", 3, True, None, None))
    assert leagues.get("espn:5").name == "renamed" and len(leagues.all_leagues()) == 2


def test_key_for():
    assert leagues.key_for("espn", 100000001) == "espn:100000001"


def test_nfl_season():
    assert config.nfl_season(date(2026, 10, 24)) == 2026
    assert config.nfl_season(date(2027, 1, 20)) == 2026
    assert config.nfl_season(date(2027, 3, 1)) == 2027
