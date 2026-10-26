"""The league registry: the home league's facts live in the `leagues` table, never in .env."""
from datetime import date

import pytest
import sqlalchemy as sa

from ffl import config, db, leagues
from ffl.leagues import LeagueRef


def _clear():
    with db._write() as c:
        c.execute(sa.delete(db.leagues_table))


def test_migration_seeds_home_from_env(monkeypatch, tmp_path, empty_db):
    from alembic import command
    monkeypatch.setattr(config, "DATA", tmp_path)       # 0015 / 0017 / 0019 import files from DATA: there are none
    monkeypatch.setattr(config, "JOURNAL", tmp_path)    # 0019 imports journal/draft/*.csv: none here either
    monkeypatch.setenv("FFL_DB_URL", empty_db())
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "0014")
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    assert leagues.home() == LeagueRef("espn:100000001", "espn", "100000001", 2026, "home", 7, True, None, "2qb-10team")
    assert len(leagues.all_leagues()) == 1


def test_migration_without_env_seeds_nothing(monkeypatch, tmp_path, empty_db):
    from alembic import command
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "JOURNAL", tmp_path)
    monkeypatch.setenv("FFL_DB_URL", empty_db())
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


OTHER = LeagueRef("espn:999", "espn", "999", 2026, "other", None, False, None, None)


def test_league_is_cached_per_league(monkeypatch):
    import espn_api.football as fb
    made = []
    monkeypatch.setattr(fb, "League", lambda **kw: made.append(kw) or object())
    config.league.cache_clear()
    home = leagues.home()
    a = config.league(home)
    assert config.league(home) is a and config.league() is a
    config.league(OTHER)
    assert [m["league_id"] for m in made] == [100000001, 999]
    config.league.cache_clear()


def test_raw_league_get_uses_the_ref(monkeypatch):
    from types import SimpleNamespace
    seen = []
    fake = lambda ref: SimpleNamespace(espn_request=SimpleNamespace(
        league_get=lambda **kw: seen.append((ref.provider_id, kw)) or {}))
    monkeypatch.setattr(config, "league", fake)
    config.raw_league_get("mRoster", ref=OTHER, scoringPeriodId=3)
    assert seen == [("999", {"params": {"view": "mRoster", "scoringPeriodId": 3}, "headers": None, "extend": ""})]


def test_draft_room_and_league_url():
    assert config.draft_room_url(OTHER) == "https://fantasy.espn.com/football/draft?leagueId=999"
    assert config.league_url(OTHER) == "https://fantasy.espn.com/football/league?leagueId=999"


def test_config_getattr_only_serves_its_own_names():
    assert not hasattr(config, "NOPE")
    assert getattr(config, "NOPE", 1) == 1


def _raw(monkeypatch, swid="{ME}"):
    from ffl import settings
    raw = {"settings": {"name": "The Other One", "size": 2,
                        "scoringSettings": {"scoringItems": [{"statId": 53, "points": 0.5}]},
                        "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 4},
                        "rosterSettings": {"lineupSlotCounts": {"0": 1, "20": 2}, "positionLimits": {"1": 3}},
                        "draftSettings": {}, "acquisitionSettings": {}},
           "status": {"firstScoringPeriod": 1},
           "teams": [{"id": 2, "owners": ["{X}"]}, {"id": 5, "owners": ["{ME}"]}]}
    monkeypatch.setattr(config, "SWID", swid)
    monkeypatch.setattr(config, "raw_league_get", lambda view, **kw: raw)
    monkeypatch.setattr(settings, "raw_league_get", lambda view, **kw: raw)


def test_add_registers_name_and_my_team(monkeypatch):
    from ffl import formats, settings
    _raw(monkeypatch)
    ref = leagues.add("999", season=2025)
    lineup = formats.lineup_key(formats.lineup_from_settings(settings.load(ref)))
    assert ref == LeagueRef("espn:999", "espn", "999", 2025, "The Other One", 5, False, lineup, None)
    assert leagues.get("espn:999") == ref
    assert settings.load(ref)["scoring"] == [{"stat_id": 53, "abbr": "REC", "label": "Each reception", "points": 0.5}]
    assert leagues.caps(ref) == {"QB": 3}, "the league's limit, no cap of mine yet"


def test_add_without_my_swid_leaves_the_team_empty(monkeypatch):
    _raw(monkeypatch, swid="{NOBODY}")
    assert leagues.add("999").team_id is None


def test_add_refuses_the_home_league_and_a_second_home(monkeypatch):
    _raw(monkeypatch)
    with pytest.raises(SystemExit, match="already the home"):
        leagues.add("100000001")
    with pytest.raises(SystemExit, match="home league is already"):
        leagues.add("999", make_home=True)


def test_add_home_on_an_empty_registry(monkeypatch):
    _clear()
    _raw(monkeypatch)
    assert leagues.add("999", make_home=True).home is True


def test_nfl_season():
    assert config.nfl_season(date(2026, 10, 24)) == 2026
    assert config.nfl_season(date(2027, 1, 20)) == 2026
    assert config.nfl_season(date(2027, 3, 1)) == 2027



def test_rename_keeps_everything_but_the_name():
    other = leagues.register(LeagueRef("espn:999", "espn", "999", 2026, "Indianapolis Pro H2H Points PPR League", 3,
                                       False, None, "1qb-10team"))
    out = leagues.rename(other, "  Indy  ")
    assert out == LeagueRef("espn:999", "espn", "999", 2026, "Indy", 3, False, None, "1qb-10team")
    assert leagues.get("espn:999").name == "Indy" and leagues.home().home
    for bad in ("", "   ", "x" * 61):
        with pytest.raises(ValueError):
            leagues.rename(other, bad)
    assert leagues.get("espn:999").name == "Indy"
