"""Scoring and lineup formats shared across leagues; league settings and caps in the DB (ffl-soc, ffl-twi)."""
import copy
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from ffl import db, formats, leagues, settings, sleeper
from ffl.leagues import LeagueRef

FX = Path(__file__).parent / "fixtures" / "home_settings.json"
HOME = "espn:100000001"


def fixture() -> dict:
    return json.loads(FX.read_text())


def item(stat_id: int, points: float, **overrides) -> dict:
    """A raw ESPN scoringItem; overrides are keyed by lineup slot id, as ESPN sends them (strings)."""
    it = {"statId": stat_id, "points": points}
    if overrides:
        it["pointsOverrides"] = {k.removeprefix("s"): v for k, v in overrides.items()}
    return it


PPR = [item(3, 0.04), item(4, 4), item(20, -2), item(24, 0.1), item(25, 6), item(42, 0.1), item(43, 6),
       item(53, 1), item(72, -2), item(80, 3), item(86, 1), item(89, 5), item(95, 2), item(99, 1)]


def keys(tables: dict) -> dict:
    return {pos: formats.scoring_key(pos, t) for pos, t in tables.items()}


def other_league(n: int = 999) -> LeagueRef:
    return leagues.register(LeagueRef(f"espn:{n}", "espn", str(n), 2026, f"league {n}", None, False, None, None))


def scoring_format_count() -> int:
    with db._read() as c:
        return c.execute(sa.select(sa.func.count()).select_from(db.scoring_formats)).scalar()


def test_same_scoring_shares_formats():
    a, _ = formats.scoring_from_espn(PPR)
    b, _ = formats.scoring_from_espn(list(reversed(PPR)))
    assert set(a) == set(formats.POSITIONS)
    assert keys(a) == keys(b), "the order ESPN lists items in is not part of a format"
    formats.store(leagues.home(), {**fixture(), "scoring_items": PPR})
    formats.store(other_league(), {**fixture(), "league_id": 999, "scoring_items": list(reversed(PPR))})
    assert scoring_format_count() == 6, "two leagues on one scoring share six rows, not twelve"


def test_te_premium_shares_all_but_te():
    plain, _ = formats.scoring_from_espn(PPR)
    premium, _ = formats.scoring_from_espn([item(53, 1, s6=1.5) if i["statId"] == 53 else i for i in PPR])
    kp, kq = keys(plain), keys(premium)
    assert {p for p in formats.POSITIONS if kp[p] != kq[p]} == {"TE"}
    assert premium["TE"]["rec"] == 1.5 and premium["WR"]["rec"] == 1.0


def test_dst_override_stays_on_dst():
    """ffl-twi: collect took the slot-16 override for every position; a D/ST override is D/ST's alone."""
    t, _ = formats.scoring_from_espn([item(63, 6, s16=2)])   # FTD: offense scores it at the base points
    assert "fum_rec_td" not in t["D/ST"], "a D/ST does not score an offensive fumble-recovery TD"
    assert all(t[p]["fum_rec_td"] == 6 for p in ("QB", "RB", "WR", "TE"))
    t, _ = formats.scoring_from_espn([item(101, 6, s16=3)])  # KRTD: a stat both a player and a D/ST score
    assert t["D/ST"]["kr_td"] == 3 and t["WR"]["kr_td"] == 6


def test_an_override_of_zero_is_a_zero():
    t, _ = formats.scoring_from_espn([item(53, 1, s6=0)])
    assert "rec" not in t["TE"] and t["WR"]["rec"] == 1


def test_unprojected_stat_differs_only_scoring_key():
    a, _ = formats.scoring_from_espn(PPR + [item(63, 6)])
    b, _ = formats.scoring_from_espn(PPR + [item(63, 2)])
    assert formats.scoring_key("WR", a["WR"]) != formats.scoring_key("WR", b["WR"])
    assert formats.rescore_key("WR", a["WR"]) == formats.rescore_key("WR", b["WR"])


def test_points_are_normalized_before_hashing():
    a, _ = formats.scoring_from_espn([item(53, 1)])
    b, _ = formats.scoring_from_espn([item(53, 1.0), item(42, 0)])
    assert keys(a) == keys(b), "1 and 1.0 are one value; a stat worth 0 is a stat not scored"


def test_unmapped_abbr_is_recorded():
    _, unmapped = formats.scoring_from_espn(PPR + [item(155, 1), item(9999, 2), item(156, 0)])
    assert unmapped == ["TW", "stat9999"], "a zero-point item scores nothing, mapped or not"
    formats.store(leagues.home(), {**fixture(), "scoring_items": PPR + [item(155, 1)]})
    with db._read() as c:
        rows = c.execute(sa.select(db.league_scoring).where(db.league_scoring.c.league == HOME)).all()
    assert len(rows) == 6 and all(r.unmapped == ["TW"] for r in rows)


def test_home_list_maps_every_stat():
    """The home league's flat list (settings.json) has nothing the formats cannot place."""
    _, unmapped = formats.scoring_from_espn(formats.items_from_list(fixture()["scoring"]))
    assert unmapped == []


def test_load_round_trips_todays_settings():
    fx = fixture()
    ref = formats.store(leagues.home(), copy.deepcopy(fx))
    assert ref.lineup_format == formats.lineup_key(formats.lineup_from_settings(fx))
    assert ref.name == "2026 Fantasy Football" and ref.team_id == 7, "the registry row keeps its team"
    assert settings.load(leagues.home()) == {**fx, "first_week": 1, "league_key": HOME}
    assert settings.load() == settings.load(leagues.home()), "no league named: the home league"


def test_load_keeps_the_lineup_order():
    fx = fixture()
    formats.store(leagues.home(), copy.deepcopy(fx))
    got = settings.load()
    assert list(got["starters"]) == list(fx["starters"]) and list(got["roster_slots"]) == list(fx["roster_slots"])


def test_store_keeps_raw_items_out_of_load_and_takes_first_week():
    formats.store(leagues.home(), {**fixture(), "scoring_items": PPR, "first_week": 2})
    got = settings.load()
    assert "scoring_items" not in got and got["first_week"] == 2
    assert formats.league_table(leagues.home())["QB"]["pass_int"] == -2


def test_league_table_is_per_position():
    formats.store(leagues.home(), {**fixture(), "scoring_items": [item(53, 1, s6=1.5), item(89, 0, s16=5)]})
    t = formats.league_table(leagues.home())
    assert t["TE"] == {"rec": 1.5} and t["WR"] == {"rec": 1.0} and t["D/ST"] == {"pa_0": 5.0}


def test_load_without_settings_says_what_to_run():
    with pytest.raises(SystemExit, match="ffl.settings"):
        settings.load()


def test_pass_int_is_intt():
    """ffl-twi: `int` is the interception thrown (INTT), not the D/ST interception (INT), and is applied signed."""
    scoring = [{"stat_id": 95, "abbr": "INT", "label": "Each Interception", "points": 2.0},
               {"stat_id": 20, "abbr": "INTT", "label": "Interceptions Thrown", "points": -1.0}]
    ks = settings.key_scoring(scoring)
    assert ks["int"] == -1
    assert sleeper.score({"pass_int": 1}, ks) == -1


def test_home_rescoring_unchanged():
    ks = settings.key_scoring(fixture()["scoring"])
    assert ks["int"] == -2.0
    assert sleeper.score({"pass_int": 2, "pass_yd": 250, "pass_td": 2}, ks) == pytest.approx(-4 + 10 + 8)


def test_caps_take_the_lower():
    formats.store(leagues.home(), fixture())   # provider limits QB 4, RB 8, WR 8, TE 3, K 3, D/ST 3
    home = leagues.home()
    leagues.set_cap(home, "QB", 3)
    leagues.set_cap(home, "K", 3)
    caps = leagues.caps(home)
    assert caps == {"QB": 3, "RB": 8, "WR": 8, "TE": 3, "K": 3, "D/ST": 3}
    ref = other_league()   # no settings stored: no provider limits
    assert leagues.caps(ref) == {}
    leagues.set_cap(ref, "TE", 2)
    assert leagues.caps(ref) == {"TE": 2}, "no league limit: my cap alone"


def test_my_cap_above_provider_limit_refused(capsys):
    formats.store(leagues.home(), fixture())
    with pytest.raises(SystemExit, match="the league allows at most 4 QB"):
        leagues.main(["caps", HOME, "QB=5"])
    assert leagues.caps(leagues.home())["QB"] == 4, "a refused cap stores nothing"
    assert leagues.main(["caps", HOME, "QB=3", "D/ST=1"]) == 0
    assert leagues.caps(leagues.home()) == {"QB": 3, "RB": 8, "WR": 8, "TE": 3, "K": 3, "D/ST": 1}
    capsys.readouterr()
    assert leagues.main(["caps", HOME]) == 0
    out = capsys.readouterr().out
    assert "QB" in out and "D/ST" in out


def test_restore_keeps_my_caps():
    formats.store(leagues.home(), fixture())
    leagues.set_cap(leagues.home(), "RB", 7)
    formats.store(leagues.home(), fixture())
    assert leagues.caps(leagues.home())["RB"] == 7, "re-reading the league's settings keeps my caps"


def test_migration_imports_settings_json(tmp_path, monkeypatch):
    from alembic import command
    from ffl import config
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(FX.read_text())
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setenv("LEAGUE_ID", "100000001")
    monkeypatch.setenv("SEASON", "2026")
    monkeypatch.setenv("MY_TEAM_ID", "7")
    db.reset()
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    home = leagues.home()
    assert settings.load(home) == {**fixture(), "first_week": 1, "league_key": HOME}
    assert leagues.caps(home) == {"QB": 3, "RB": 7, "WR": 7, "TE": 2, "K": 1, "D/ST": 1}
    assert home.name == "2026 Fantasy Football"
    assert home.lineup_format == formats.lineup_key(formats.lineup_from_settings(fixture()))
    assert formats.league_table(home)["QB"]["pass_int"] == -2.0
