"""The league registry: the home league's facts live in the `leagues` table, never in .env."""
from datetime import date

import pytest
import sqlalchemy as sa

from ffl import config, db, leagues
from ffl.leagues import LeagueRef
from tests.home import home_league


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
    assert leagues.get("espn:100000001") == LeagueRef("espn:100000001", "espn", "100000001", 2026, "home", None, None,
                                                      "2qb-10team"), "no user: the league row alone, nobody's team"
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
    assert home_league().provider_id == "100000001"
    assert not hasattr(config, "LEAGUE_ID") and not hasattr(config, "MY_TEAM_ID"), "no implicit league in config"


def test_no_league_registered_says_how_to_add_one():
    _clear()
    with pytest.raises(SystemExit, match=r"has no league: `uv run python -m ffl.leagues add espn <id> --default`"):
        leagues.acting()
    with pytest.raises(SystemExit, match="no league registered"):
        leagues.oldest()


def test_unknown_key_exits_with_known_keys():
    with pytest.raises(SystemExit) as e:
        leagues.get("espn:1")
    assert "espn:100000001" in str(e.value)


def test_acting_is_the_users_default(monkeypatch):
    assert leagues.acting() == home_league()


def test_acting_reads_the_env_at_call_time(monkeypatch):
    other = leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "other", 2, None, None), user_id=0)
    monkeypatch.setenv("FFL_LEAGUE", "espn:5")
    assert leagues.acting() == other
    monkeypatch.setenv("FFL_LEAGUE", "espn:100000001")
    assert leagues.acting().provider_id == "100000001"
    assert leagues.acting("espn:5") == other, "a key named beats FFL_LEAGUE"


def test_acting_needs_ffl_user_and_their_membership(monkeypatch):
    monkeypatch.delenv("FFL_USER")
    with pytest.raises(SystemExit, match=r"FFL_USER is not set: add `FFL_USER=<your sign-in email>` to .env"):
        leagues.acting()
    monkeypatch.setenv("FFL_USER", "nobody@example.com")
    with pytest.raises(SystemExit, match=r"no user 'nobody@example.com'.*ffl\.leagues user add nobody@example.com"):
        leagues.acting()
    db.ensure_user("b@example.com", None)
    monkeypatch.setenv("FFL_USER", "b@example.com")
    with pytest.raises(SystemExit, match="b@example.com has no league"):
        leagues.acting()
    with pytest.raises(SystemExit, match=r"b@example.com is not a member of 'espn:100000001'; theirs: none"):
        leagues.acting("espn:100000001")


def test_user_add_from_the_command_line(capsys):
    assert leagues.main(["user", "add", "c@example.com"]) == 0
    assert db.user_by_email("c@example.com") is not None and "c@example.com" in capsys.readouterr().out


def test_register_upserts_and_files_the_member():
    leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "mock", 2, None, None, is_default=True), user_id=0)
    assert leagues.acting().key == "espn:5", "the user's default now"
    assert [r.key for r in leagues.all_leagues()] == ["espn:100000001", "espn:5"], "oldest first"
    leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "renamed", 3, None, None), user_id=0)
    assert leagues.get("espn:5").name == "renamed" and leagues.get("espn:5").team_id == 3
    assert len(leagues.all_leagues()) == 2
    leagues.register(LeagueRef("espn:6", "espn", "6", 2026, "row only", None, None, None), user_id=None)
    assert leagues.get("espn:6").user_id is None and [r.key for r in leagues.for_user(0)] == ["espn:5", "espn:100000001"]


def test_key_for():
    assert leagues.key_for("espn", 100000001) == "espn:100000001"


OTHER = LeagueRef("espn:999", "espn", "999", 2026, "other", None, None, None)


def test_league_is_cached_per_league(monkeypatch):
    import espn_api.football as fb
    made = []
    monkeypatch.setattr(fb, "League", lambda **kw: made.append(kw) or object())
    config.league.cache_clear()
    home = home_league()
    a = config.league(home)
    assert config.league(home) is a and config.league(ref=home_league()) is a
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
    ref = leagues.add("999", season=2025, user_id=0)
    lineup = formats.lineup_key(formats.lineup_from_settings(settings.load(ref)))
    assert ref == LeagueRef("espn:999", "espn", "999", 2025, "The Other One", 5, lineup, None, user_id=0, owner=True)
    assert leagues.get("espn:999") == ref
    assert settings.load(ref)["scoring"] == [{"stat_id": 53, "abbr": "REC", "label": "Each reception", "points": 0.5}]
    assert leagues.caps(ref) == {"QB": 3}, "the league's limit, no cap of mine yet"


def test_add_refuses_a_league_already_registered(monkeypatch):
    """Re-adding would rewrite its row from ESPN's answer alone, its strategy blanked: ffl.settings re-reads it."""
    _raw(monkeypatch)
    with pytest.raises(SystemExit, match=r"espn:100000001 is already registered: re-read its settings"):
        leagues.add("100000001", user_id=0)
    assert leagues.get("espn:100000001").strategy == "2qb-10team"


def test_register_never_blanks_what_the_ref_does_not_know():
    leagues.register(LeagueRef("espn:100000001", "espn", "100000001", 2026, "home", 7, None, None), user_id=None)
    assert leagues.get("espn:100000001").strategy == "2qb-10team"


def test_add_without_my_swid_leaves_the_team_empty(monkeypatch):
    _raw(monkeypatch, swid="{NOBODY}")
    assert leagues.add("999", user_id=0).team_id is None


def test_add_as_the_users_default(monkeypatch):
    _raw(monkeypatch)
    ref = leagues.add("999", user_id=0, default=True)
    assert ref.is_default and leagues.acting() == ref and not home_league().is_default


def test_add_from_the_command_line_files_it_under_ffl_user(monkeypatch, capsys):
    _raw(monkeypatch)
    assert leagues.main(["add", "espn", "999"]) == 0
    assert leagues.membership(0, "espn:999").team_id == 5 and "team 5" in capsys.readouterr().out


def test_nfl_season():
    assert config.nfl_season(date(2026, 10, 24)) == 2026
    assert config.nfl_season(date(2027, 1, 20)) == 2026
    assert config.nfl_season(date(2027, 3, 1)) == 2027



def test_rename_keeps_everything_but_the_name():
    other = leagues.register(LeagueRef("espn:999", "espn", "999", 2026, "Indianapolis Pro H2H Points PPR League", 3,
                                       None, "1qb-10team"), user_id=0)
    out = leagues.rename(other, "  Indy  ")
    assert out == LeagueRef("espn:999", "espn", "999", 2026, "Indy", 3, None, "1qb-10team", user_id=0, owner=True)
    assert leagues.get("espn:999").name == "Indy" and home_league().name == "home"
    for bad in ("", "   ", "x" * 61):
        with pytest.raises(ValueError):
            leagues.rename(other, bad)
    assert leagues.get("espn:999").name == "Indy"
