"""espn_season's League, my team and box/gap/rank caches are per league (ffl-soc.4.3)."""
import json
from types import SimpleNamespace as NS

import pytest

from ffl import config, db, espn_season, leagues

SETTINGS = {"league_id": 999, "teams": [{"id": 3, "name": "Team 3", "owner_swids": []}]}


@pytest.fixture
def other():
    return leagues.register(leagues.LeagueRef("espn:999", "espn", "999", 2026, "other", 3, False, None, None))


def test_config_league_caches_per_league_and_evicts_one(monkeypatch, other):
    import espn_api.football as football

    built = []

    class FakeLeague:
        def __init__(self, league_id, year, **kw):
            built.append(league_id)
            self.league_id = league_id

    monkeypatch.setattr(football, "League", FakeLeague)
    config.league.cache_clear()
    home = leagues.home()
    h, o = config.league(home), config.league(other)
    assert h is not o and h.league_id == int(home.provider_id) and o.league_id == 999
    assert config.league(home) is h and config.league(other) is o and len(built) == 2
    assert config.league() is h, "no ref is home"
    config.league.evict(other)
    assert config.league(home) is h, "home's League survived the eviction"
    assert config.league(other) is not o and len(built) == 3
    config.league.cache_clear()
    assert config.league(home) is not h and len(built) == 4
    config.league.cache_clear()


def test_fresh_league_is_per_league(monkeypatch, other):
    cache, asked = {}, []

    def build(ref=None, *a, **k):
        asked.append(ref)
        return cache.setdefault(ref.key, NS(key=ref.key, n=len(asked)))
    build.evict = lambda ref: cache.pop(ref.key, None)
    monkeypatch.setattr(espn_season, "league", build)
    home = leagues.home()
    h, o = espn_season.fresh_league(), espn_season.fresh_league(ref=other)
    assert asked == [home, other] and h is not o and (h.key, o.key) == (home.key, other.key)
    assert espn_season.fresh_league(other) is o and espn_season.fresh_league() is h, "inside the TTL the same League"
    espn_season._BUILT[other.key] = 0
    assert espn_season.fresh_league(other) is not o, "other's League was rebuilt"
    assert espn_season.fresh_league() is h, "home's was not"


def test_fresh_league_evicts_only_its_own_league(monkeypatch, other):
    evicted = []

    def build(ref=None, *a, **k):
        return NS()
    build.evict = evicted.append
    monkeypatch.setattr(espn_season, "league", build)
    espn_season.fresh_league(other)
    espn_season.fresh_league()
    assert evicted == [other, leagues.home()]
    espn_season._BUILT[other.key] = 0
    espn_season.fresh_league(other)
    assert evicted == [other, leagues.home(), other]


def test_my_team_id_per_league(monkeypatch, other):
    monkeypatch.setattr(config, "SWID", "{NOT-IN-ANY-LEAGUE}")
    monkeypatch.setattr(espn_season, "load", lambda ref=None: {**SETTINGS, "league_id": int((ref or leagues.home()).provider_id)})
    assert espn_season.my_team_id(None, other) == 3
    assert espn_season.my_team_id(None) == leagues.home().team_id


def _lg(rows):
    calls = []

    def box_scores(week):
        calls.append(week)
        return rows
    return NS(current_week=5, box_scores=box_scores), calls


def test_box_cache_is_per_league(other):
    h, hcalls = _lg(["home box"])
    o, ocalls = _lg(["other box"])
    assert espn_season.box_scores(h, 5) == ["home box"]
    assert espn_season.box_scores(o, 5, ref=other) == ["other box"]
    assert espn_season.box_scores(h, 5) == ["home box"] and espn_season.box_scores(o, 5, ref=other) == ["other box"]
    assert hcalls == [5] and ocalls == [5]


def test_finished_boxes_are_per_league_too(other):
    h = NS(current_week=9, box_scores=lambda w: ["home done"])
    o = NS(current_week=9, box_scores=lambda w: ["other done"])
    assert espn_season.box_scores(h, 5) == ["home done"]
    assert espn_season.box_scores(o, 5, ref=other) == ["other done"], "home's finished week 5 does not answer other's"


def test_forget_current_boxes_only_that_league(other):
    h, hcalls = _lg(["home box"])
    o, ocalls = _lg(["other box"])
    espn_season.box_scores(h, 5)
    espn_season.box_scores(o, 5, ref=other)
    espn_season.forget_current_boxes(ref=other)
    espn_season.box_scores(h, 5)
    espn_season.box_scores(o, 5, ref=other)
    assert hcalls == [5] and ocalls == [5, 5]
    espn_season.forget_current_boxes()
    espn_season.box_scores(h, 5)
    espn_season.box_scores(o, 5, ref=other)
    assert hcalls == [5, 5] and ocalls == [5, 5]


def test_gap_games_per_league(monkeypatch, other):
    home_rows = [{"pts": 1.0}]
    espn_season._GAP_GAMES[(leagues.home().key, 11, 5)] = home_rows
    asked = []

    def fetch(w, ids, week, *, ref=None):
        asked.append(ref)
        return {i: [{"pts": 9.0}] for i in ids}
    monkeypatch.setattr(espn_season, "_fetch_gap_rows", fetch)
    assert espn_season._gap_games({11: [5]}, 8, ref=other) == {11: [{"pts": 9.0}]}
    assert asked == [other]
    assert db.gap_rows(5, [11], ref=other) == {11: [{"pts": 9.0}]}
    assert db.gap_rows(5, [11]) == {}, "home's table is untouched"
    assert espn_season._gap_games({11: [5]}, 8) == {11: home_rows}, "home still reads its own memory"
    assert asked == [other]


def test_ranks_per_league(monkeypatch, other):
    asked = []

    def get(view, headers=None, *, ref=None, **k):
        asked.append(ref)
        return {}
    monkeypatch.setattr(espn_season, "raw_league_get", get)
    from ffl import player_card
    monkeypatch.setattr(player_card, "parse_ranks", lambda doc: {"id": len(asked)})
    monkeypatch.setattr(player_card, "parse_pos_ranks", lambda doc, pos, season: {"id": len(asked)})
    a, b = espn_season.opponent_ranks(), espn_season.opponent_ranks(ref=other)
    assert a != b and espn_season.opponent_ranks() == a and espn_season.opponent_ranks(ref=other) == b
    c, d = espn_season.position_ranks("WR"), espn_season.position_ranks("WR", ref=other)
    assert c != d and espn_season.position_ranks("WR") == c and len(asked) == 4


def test_no_ref_is_home_even_with_ffl_league(monkeypatch, other):
    monkeypatch.setenv("FFL_LEAGUE", "espn:999")
    got = []
    monkeypatch.setattr(espn_season, "league", lambda ref=None, *a, **k: got.append(ref) or NS())
    espn_season.fresh_league()
    assert got == [leagues.home()]
    seen = []
    monkeypatch.setattr(espn_season, "load", lambda ref=None: seen.append(ref) or {"teams": []})
    monkeypatch.setattr(config, "SWID", "{NOBODY}")
    assert espn_season.my_team_id(None) == leagues.home().team_id and seen == [leagues.home()]
