"""Strategy presets live in the DB (STRATEGY.md's text is the first); my caps are league data seeded from the preset."""
from datetime import datetime

import pytest
import sqlalchemy as sa

from ffl import db, leagues, strategy
from ffl.leagues import LeagueRef

HOME_CAPS = {"QB": 3, "RB": 7, "WR": 7, "TE": 2, "K": 1, "D/ST": 1}
LIMITS = {"QB": 4, "RB": 8, "WR": 8, "TE": 3, "K": 3, "D/ST": 3}


def add_version(key: str, version: int, constants: dict) -> None:
    """What a later migration does: a new version of a preset (presets are never edited in place)."""
    with db._write() as c:
        c.execute(sa.insert(db.strategies).values(key=key, version=version, name=f"{key} v{version}",
                                                  prose=f"# {key} v{version}\n", constants=constants,
                                                  added_at=datetime.now().isoformat()))


def provider_limits(league: str, limits: dict) -> None:
    """What formats.store leaves for a new league: the provider's limits, no caps of mine."""
    with db._write() as c:
        c.execute(sa.insert(db.league_caps), [{"league": league, "pos": p, "provider_limit": n, "my_cap": None}
                                              for p, n in limits.items()])


def test_home_strategy_is_seeded():
    home = leagues.home()
    s = strategy.for_league(home)
    assert s.key == "2qb-10team" and s.version == 1
    assert s.prose.startswith('# Draft strategy — "2026 Fantasy Football" (ESPN league 100000001)\n')
    assert "## Waivers (in-season, 2026-09-08)" in s.prose and "## Lineup (in-season, 2026-09-08)" in s.prose
    assert s.constants == {"qb_pace": 0.0, "suggested_caps": HOME_CAPS}
    assert strategy.caps(home) == HOME_CAPS
    assert strategy.caps() == HOME_CAPS, "no league named: the current (home) league"


def test_new_league_starts_from_suggested_caps_then_owns_them():
    ref = leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "other", 2, False, None, "2qb-10team"))
    provider_limits(ref.key, LIMITS)
    assert strategy.caps(ref) == HOME_CAPS
    assert leagues.main(["caps", ref.key, "TE=3"]) == 0
    add_version("2qb-10team", 2, {"qb_pace": 2.0, "suggested_caps": {**HOME_CAPS, "TE": 1, "RB": 6}})
    assert strategy.for_league(ref).version == 2
    assert strategy.caps(ref) == {**HOME_CAPS, "TE": 3}, "a new preset version moved a cap the league owns"


def test_a_cap_set_before_the_first_read_is_kept():
    ref = leagues.register(LeagueRef("espn:5", "espn", "5", 2026, "other", 2, False, None, "2qb-10team"))
    provider_limits(ref.key, LIMITS)
    leagues.set_cap(ref, "TE", 3)
    assert strategy.caps(ref) == {**HOME_CAPS, "TE": 3}


def test_suggested_caps_never_exceed_the_league_limit():
    ref = leagues.register(LeagueRef("espn:6", "espn", "6", 2026, "tight", 2, False, None, "2qb-10team"))
    provider_limits(ref.key, {"QB": 2})
    assert strategy.caps(ref)["QB"] == 2
    assert db.caps_rows(ref.key)["QB"]["my_cap"] == 2


def test_a_league_without_a_preset_keeps_its_own_caps():
    ref = leagues.register(LeagueRef("espn:7", "espn", "7", 2026, "bare", 2, False, None, None))
    assert strategy.caps(ref) == {}
    assert strategy.qb_pace(ref) == 0.0
    with pytest.raises(SystemExit, match="espn:7"):
        strategy.for_league(ref)


def test_qb_pace_env_overrides_the_preset(monkeypatch):
    monkeypatch.delenv("FFL_QB_PACE", raising=False)
    assert strategy.qb_pace() == 0.0
    add_version("2qb-10team", 2, {"qb_pace": 3.5, "suggested_caps": HOME_CAPS})
    assert strategy.qb_pace() == 3.5
    monkeypatch.setenv("FFL_QB_PACE", "10")
    assert strategy.qb_pace() == 10.0
    monkeypatch.setenv("FFL_QB_PACE", "fast")
    assert strategy.qb_pace() == 0.0


def test_cli_show_and_list(capsys):
    assert strategy.main(["show"]) == 0
    assert capsys.readouterr().out.startswith("# Draft strategy")
    assert strategy.main(["show", "--league", "espn:100000001"]) == 0
    assert capsys.readouterr().out.startswith("# Draft strategy")
    assert strategy.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "2qb-10team  v1" in out and "espn:100000001" in out


def test_public_preset_seeded():
    s = db.strategy_row("1qb-10team")
    assert s["version"] == 1 and s["name"] == "1QB, 10 teams"
    assert s["constants"] == {"qb_pace": 0.0, "suggested_caps": LIMITS}, "no caps tighter than the league's own"
    assert s["prose"].startswith("# Draft strategy — 1QB, 10 teams, ESPN standard (full PPR)\n")
    for h in ("## Draft rules (1QB)", "## Waivers (in-season, 2026-09-08)", "## Lineup (in-season, 2026-09-08)"):
        assert h in s["prose"]
    assert "Props trial" not in s["prose"] and "Brier 0.203" not in s["prose"], "the home league's history stays home's"


def test_0020_links_the_public_league_where_it_is_registered(tmp_path, monkeypatch):
    from alembic import command
    from ffl import config
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(config, "JOURNAL", tmp_path / "journal")
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    monkeypatch.setenv("FFL_DB_URL", url)
    monkeypatch.delenv("LEAGUE_ID", raising=False)
    db.reset()
    command.upgrade(db._alembic_config(), "0019")
    eng = sa.create_engine(url)  # the app refuses a DB behind head, so the row goes in as 0019 left the table
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO leagues (key, provider, provider_id, season, name, team_id, home, added_at) "
                          "VALUES ('espn:950458410', 'espn', '950458410', 2026, 'public', 1, 0, '2026-10-25')"))
    eng.dispose()
    command.upgrade(db._alembic_config(), "head")
    ref = leagues.get("espn:950458410")
    assert ref.strategy == "1qb-10team"
    provider_limits(ref.key, LIMITS)
    assert strategy.caps(ref) == LIMITS
    command.downgrade(db._alembic_config(), "0019")
    assert leagues.get("espn:950458410").strategy is None
    assert db.strategy_row("1qb-10team") is None
    db.reset()
