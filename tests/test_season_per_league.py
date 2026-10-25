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


# ---- the page builders take the league they read (ffl-soc.4.3, task 2) ----

from types import SimpleNamespace as SN  # noqa: E402

from ffl import espn_write  # noqa: E402
from tests.test_espn_season import as_box, season_roster  # noqa: E402
from tests.test_season import TWO_QB  # noqa: E402

HOME_SET = {**TWO_QB, "scoring": [], "reg_season_weeks": 14, "playoff_teams": 6, "playoff_weeks": [15, 16], "first_week": 1}
OTHER_SET = {**TWO_QB, "scoring": [], "reg_season_weeks": 12, "playoff_teams": 4, "playoff_weeks": [13, 14, 15], "first_week": 3}


def _team(i, name):
    return SN(team_id=i, team_name=name, wins=0, losses=0, ties=0, owners=[], points_for=0.0, points_against=0.0)


def _fake_league(me, them, calls, tag):
    """A League whose rosters are team `me`'s and `them`'s; every free-agent ask is logged with the League's tag."""
    roster = [as_box(p) for p in season_roster()]
    m = SN(home_team=_team(me, f"{tag} mine"), home_lineup=roster, home_score=0, home_projected=0,
           away_team=_team(them, f"{tag} them"), away_lineup=roster[:3], away_score=0, away_projected=0)

    def free_agents(week, size, position):
        calls["fa"].append((tag, position))
        return [as_box({**season_roster()[0], "player_id": 1000 + len(calls["fa"]), "slot": "FA"})]
    return SN(box_scores=lambda week: [m], scoreboard=lambda week: [m], free_agents=free_agents, current_week=3,
              teams=[_team(me, "a"), _team(them, "b")], standings=lambda: [_team(me, "a"), _team(them, "b")],
              _get_pro_schedule=lambda w: {}, _get_positional_ratings=lambda w: {}, tag=tag)


@pytest.fixture
def wired(monkeypatch, other):
    """Home (team 7) and other (team 3) each with their own League and settings; every load, raw read and
    my_team_id is logged with the ref it got."""
    calls = {"fa": [], "load": [], "raw": [], "team": []}
    lgs = {leagues.home().key: _fake_league(7, 2, calls, "home"), other.key: _fake_league(3, 2, calls, "other")}

    def league(ref=None, *a, **k):
        return lgs[(ref or leagues.home()).key]

    def load(ref=None):
        calls["load"].append(ref)
        return dict(OTHER_SET if ref is not None and ref.key == other.key else HOME_SET)

    def raw(view, extend="", headers=None, *, ref=None, **params):
        calls["raw"].append((view, ref))
        return {}

    def team(lg, ref=None):
        calls["team"].append(ref)
        return 3 if ref is not None and ref.key == other.key else 7
    monkeypatch.setattr(espn_season, "league", league)
    monkeypatch.setattr(espn_season, "load", load)
    monkeypatch.setattr(espn_season, "raw_league_get", raw)
    monkeypatch.setattr(espn_season, "my_team_id", team)
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, *, ref=None: calls["raw"].append(("mRoster", ref)) or {})
    return SN(calls=calls, lgs=lgs, other=other)


NOSRC = dict(sources={}, season={}, injuries={})


def _sat(side):
    return {p["player_id"] for p in side["lineup"] + side["bench"] + side["ir"] if p.get("sat")}


def test_week_state_other_league(wired):
    db.put_sit(3, 5, True)  # home's sit on player 5 ...
    db.put_sit(3, 4, True, ref=wired.other)  # ... other's on player 4
    st = espn_season.week_state(week=3, ref=wired.other, **NOSRC)
    assert st["mine"] is True and st["me"]["team_id"] == 3 and st["me"]["team"] == "other mine"
    assert st["opp"]["team"] == "other them"
    assert _sat(st["me"]) == {4}, "other's sit marks his player; home's sit on 5 does not"
    assert wired.calls["load"] and all(r.key == wired.other.key for r in wired.calls["load"])
    assert all(r.key == wired.other.key for r in wired.calls["team"])
    home = espn_season.week_state(week=3, **NOSRC)
    assert home["me"]["team_id"] == 7 and home["me"]["team"] == "home mine" and _sat(home["me"]) == {5}


def test_roster_state_other_league(wired, monkeypatch):
    seen = []
    real = db.seat_map
    monkeypatch.setattr(db, "seat_map", lambda *a, **k: seen.append(k.get("ref")) or real(*a, **k))
    out = espn_season.roster_state(week=3, snaps={}, ref=wired.other, **NOSRC)
    assert out["cards"]
    assert ("mRoster", wired.other) in wired.calls["raw"], "the slots are read from other's mRoster"
    assert seen == [wired.other]
    assert {r.key for r in wired.calls["team"]} == {wired.other.key}


def test_roster_state_other_league_ignores_home_fake_slots(wired, monkeypatch):
    monkeypatch.setenv("FFL_WRITES", "0")
    monkeypatch.setitem(espn_write._fake(leagues.home()).slots, 1, "BE")  # home moved player 1 to the bench
    flags = {1: {"slot": "QB", "order": 0, "locked": False}}
    mine = espn_season.roster_state(week=3, snaps={}, flags=flags, **NOSRC)
    theirs = espn_season.roster_state(week=3, snaps={}, flags=flags, ref=wired.other, **NOSRC)

    def slot(out):
        return next(c for c in out["cards"] if c["player_id"] == 1)["slot"]
    assert slot(mine) == "BE", "home shows its fake move"
    assert slot(theirs) == "QB", "other keeps ESPN's slot"


def test_free_agent_pool_other_league(wired):
    db.put_watch(99, True, ref=wired.other)
    db.put_watch(98, True)
    out = espn_season.free_agent_pool(week=3, snaps={}, ref=wired.other, **NOSRC)
    assert out["roster"]
    assert {t for t, _ in wired.calls["fa"]} == {"other"}, "free agents are asked of other's League"
    assert db.snapshots(ref=wired.other) and not db.snapshots(), "the calibration snapshot is other's"
    assert ("kona_player_info", wired.other) in wired.calls["raw"] and ("mRoster", wired.other) in wired.calls["raw"]
    assert all(ref is None or ref.key == wired.other.key for _, ref in wired.calls["raw"])
    assert {r.key for r in wired.calls["team"]} == {wired.other.key}
    # the watched ids are other's: 99 is read by id, home's 98 is not
    assert ("kona_player_info", wired.other) in wired.calls["raw"]


def test_league_state_other_league(wired):
    st = espn_season.league_state(week=3, ref=wired.other)
    assert st["standings"][0]["team_id"] == 3 and st["standings"][0]["me"] is True
    assert (st["weeks"], st["playoff_teams"], st["playoff_weeks"], st["first_week"]) == (15, 4, [13, 14, 15], 3)
    home = espn_season.league_state(week=1)
    assert (home["weeks"], home["playoff_teams"], home["playoff_weeks"], home["first_week"]) == (16, 6, [15, 16], 1)
    assert home["standings"][0]["team_id"] == 7 and home["standings"][0]["me"] is True


def test_player_card_other_league(wired, monkeypatch):
    from ffl import player_card as card_log
    asked, seen = [], {}
    p = {"fullName": "A B", "defaultPositionId": 2, "stats": []}
    monkeypatch.setattr(espn_season, "raw_league_get", lambda view, extend="", headers=None, *, ref=None, **k:
                        asked.append((view, ref)) or {"players": [{"player": p}]})
    monkeypatch.setattr(espn_season, "opponent_ranks", lambda *, ref=None: asked.append(("ranks", ref)) or {})
    monkeypatch.setattr(espn_season, "position_ranks", lambda pos, *, ref=None: asked.append(("pos", ref)) or {})
    monkeypatch.setattr(espn_season, "past_schedule", lambda yr: {})
    monkeypatch.setattr(espn_season, "past_stats", lambda yr, pid: [])
    monkeypatch.setattr(espn_season, "_schedule_doc", lambda lg: {})
    monkeypatch.setattr(espn_season, "weekly_sources", lambda w, s: {})
    monkeypatch.setattr(espn_season, "_sleeper_for", lambda w, s: {})
    monkeypatch.setattr(espn_season, "_stats_rows", lambda w: [])
    monkeypatch.setattr(card_log, "player_log", lambda *a, **k: seen.update(season=a[4]) or {"log": []})
    out = espn_season.player_card(1, ref=wired.other)
    refs = dict(asked)
    assert refs["kona_playercard"] == refs["ranks"] == refs["pos"] == wired.other
    assert out["playoff_weeks"] == [13, 14, 15] and seen["season"] == wired.other.season


def test_record_projections_other_league(wired, monkeypatch):
    monkeypatch.setattr(espn_season, "weekly_sources", lambda w, s: {"sleeper": {}})
    got = []
    monkeypatch.setattr(espn_season, "season_lookup", lambda s, w, ref=None: got.append(ref) or {})
    rows = [{"player_id": 1, "source": "sleeper", "points": 5.0}]
    monkeypatch.setattr(espn_season, "projection_rows_for", lambda *a, **k: (rows, []))
    out = espn_season.record_projections(ref=wired.other)
    assert out["week"] == 1 and got == [wired.other]
    assert db.projection_rows("week", ref=wired.other) and not db.projection_rows("week")


# ---- transactions, results and the draft per league (task 3) ----

def _act(team_id=3, pid=77, name="Some Guy"):
    team = NS(team_id=team_id, team_name=f"Team {team_id}")
    return NS(date=1788937502756, actions=[(team, "FA ADDED", NS(playerId=pid, name=name), 0)])


def test_sync_other_league_calls_listeners_queues_no_notices(monkeypatch, other):
    from ffl import journal
    monkeypatch.setattr(journal, "_activities", lambda lg, size: [_act()])
    heard = []
    monkeypatch.setattr(journal, "ON_NEW_ROWS", [lambda rows, ref=None: heard.append(ref)])
    new = journal.sync_transactions(NS(), ref=other)
    assert len(new) == 1
    assert [r["player"] for r in db.transactions(ref=other)] == ["Some Guy"]
    assert db.transactions() == []
    assert db.unsent_notices(ref=other) == [] and db.unsent_notices() == []
    assert heard == [other], "the listener hears another league's rows, with that league"
    # the same call with no ref is home's: it queues the notice and calls the listener
    assert len(journal.sync_transactions(NS())) == 1
    assert len(heard) == 2 and heard[1] == leagues.home() and len(db.unsent_notices()) >= 1
    assert db.unsent_notices(ref=other) == []


def test_sync_results_from_first_week(monkeypatch, other):
    from ffl import journal
    me, opp = NS(team_id=3, team_name="Me"), NS(team_id=4, team_name="Them")
    asked = []

    def scoreboard(wk):
        asked.append(wk)
        return [NS(home_team=me, away_team=opp, home_score=100.0, away_score=90.0)]
    lg = NS(current_week=10, teams=[me, opp], scoreboard=scoreboard)
    seen = []
    real = leagues.my_team_id

    def my_team(settings, ref=None):
        seen.append(ref)
        return real(settings, ref)
    monkeypatch.setattr(leagues, "my_team_id", my_team)
    monkeypatch.setattr(config, "SWID", "{NOPE}")
    journal.sync_results(lg, {**SETTINGS, "first_week": 8}, ref=other)
    assert asked == [8, 9] and seen == [other]
    assert [r["week"] for r in db.results_rows(ref=other)] == [8, 9]
    assert db.results_rows() == []


def test_draft_picks_other_league(monkeypatch, other):
    rows = [{"overall": 1, "round": 1, "pick": 1, "team_id": 3, "team": "Team 3", "player_id": 11,
             "player": "A", "pos": "QB", "nfl": "BUF", "ts": "t1"}]
    db.put_picks(rows, ref=other)
    assert espn_season.draft_picks(ref=other) == [
        {"overall": 1, "round": 1, "pick": 1, "team_id": 3, "team": "Team 3", "player_id": 11, "at": "t1"}]
    assert db.picks() == [], "home has none stored"
    asked = []
    pick = NS(round_num=1, round_pick=2, team=NS(team_id=4, team_name="Team 4"), playerId=12)

    def fresh(ref=None):
        asked.append(ref)
        return NS(draft=[pick])
    monkeypatch.setattr(espn_season, "fresh_league", fresh)
    monkeypatch.setattr(db, "picks", lambda ref=None, season=None: [])
    assert espn_season.draft_picks(ref=other)[0]["player_id"] == 12
    assert asked == [other]


def test_reconcile_per_league(monkeypatch, other):
    from ffl import journal
    synced = []
    monkeypatch.setattr(journal, "sync_transactions", lambda lg, *, ref=None: synced.append(ref) or [])
    seen = []
    monkeypatch.setattr(espn_season, "draft_picks", lambda *, ref=None: seen.append(("picks", ref)) or [])
    monkeypatch.setattr(espn_season, "journal_state", lambda *, ref=None: seen.append(("log", ref)) or {"transactions": []})
    espn_season._RECONCILED.clear()
    owners = {5: 1}
    assert espn_season.reconcile_log(NS(), owners) is True
    assert espn_season.reconcile_log(NS(), owners) is False, "home's drift synced once"
    assert espn_season.reconcile_log(NS(), owners, ref=other) is True, "the same drift for other still syncs"
    assert espn_season.reconcile_log(NS(), owners, ref=other) is False
    assert synced == [leagues.home(), other]
    assert ("picks", other) in seen and ("log", other) in seen
    assert set(espn_season._RECONCILED) == {leagues.home().key, other.key}


def test_fetch_boxes_reconciles_other_league(monkeypatch, other):
    got = []
    monkeypatch.setattr(espn_season, "_read_boxes", lambda lg, week, finished, epoch, ref: ([], True, False))
    monkeypatch.setattr(espn_season, "owners_in", lambda boxes: {})
    monkeypatch.setattr(espn_season, "reconcile_log", lambda lg, owners, *, ref=None: got.append(ref))
    espn_season._fetch_boxes(NS(current_week=5), 5, other)
    assert got == [other]


def test_transactions_state_other_league(monkeypatch, other):
    from ffl import journal
    rows = [{"id": "1788937502756-3-FA ADDED-77", "date": "2026-09-09T10:00", "type": "FA ADDED", "team": "Team 3",
             "player": "Some Guy", "action": "FA ADDED", "bid": 0, "reasoning": ""}]
    db.add_transactions(rows, ref=other)
    teams = [NS(team_id=3, team_name="Team 3", logo_url="", team_abbrev="T3")]
    fresh_asked, synced, tx_ref, loaded = [], [], [], []
    monkeypatch.setattr(espn_season, "fresh_league", lambda ref=None: fresh_asked.append(ref) or NS(teams=teams, current_week=9))
    monkeypatch.setattr(journal, "sync_transactions", lambda lg, *, ref=None: synced.append(ref) or [])
    monkeypatch.setattr(espn_season, "tx_player_info", lambda ids, *, ref=None: tx_ref.append(ref) or {77: {"pos": "WR", "team": "BUF"}})
    monkeypatch.setattr(espn_season, "load", lambda ref=None: loaded.append(ref) or {**SETTINGS, "week1_start": "2026-09-10"})
    monkeypatch.setattr(espn_season, "_club", lambda t: {"team": t.team_name, "abbrev": "T3", "logo": ""})
    monkeypatch.setattr(config, "SWID", "{NOPE}")
    got = espn_season.transactions_state(ref=other)
    assert got["me"] == 3
    assert fresh_asked == [other] and synced == [other] and tx_ref == [other] and set(loaded) == {other}
    assert len(got["moves"]) == 1 and got["moves"][0]["sides"][0]["team_id"] == 3
    assert db.transactions() == []


def _first_week(monkeypatch, other, n):
    monkeypatch.setattr(espn_season, "load", lambda ref=None: {**SETTINGS, "first_week": n if ref is not None and ref.key == other.key else 1})


def test_week_before_first_week_reads_no_boxes(monkeypatch, other):
    _first_week(monkeypatch, other, 8)

    def boom(*a, **k):
        raise AssertionError("ESPN was asked for a week before the league's first")
    lg = NS(current_week=10, box_scores=boom, espn_request=NS(league_get=boom))
    monkeypatch.setattr(espn_season, "_fetch_box_doc", boom)
    assert espn_season.box_scores(lg, 6, ref=other) == []
    assert db.box_weeks(ref=other) == {}


def test_box_scores_survives_unreadable_settings_for_home(monkeypatch):
    def broken(ref=None):
        raise RuntimeError("no settings")
    monkeypatch.setattr(espn_season, "load", broken)
    lg, calls = _lg(["home box"])
    assert espn_season.box_scores(lg, 5) == ["home box"] and calls == [5]


def test_roster_games_before_first_week_come_from_gap_reads(monkeypatch, other):
    _first_week(monkeypatch, other, 8)
    asked = []

    def fetch(w, ids, week, *, ref=None):
        asked.append((w, sorted(ids), ref))
        return {i: [{"week": w, "pts": 1.0}] for i in ids}

    def boom(*a, **k):
        raise AssertionError("no ESPN box read before the first week")
    monkeypatch.setattr(espn_season, "_fetch_gap_rows", fetch)
    monkeypatch.setattr(espn_season, "_fetch_box_doc", boom)
    monkeypatch.setattr(espn_season, "form_weeks", lambda week: [6, 7, 8])
    monkeypatch.setattr(espn_season, "player_games", lambda bp, weeks, week: [{"week": weeks[0], "pts": 2.0}])
    lg = NS(current_week=8, box_scores=boom)
    espn_season._BOXES[(other.key, 8)] = (float("inf"), lg, [])
    games = espn_season._roster_games(lg, {3: [NS(playerId=11)]}, 8, ref=other)
    assert asked == [(6, [11], other), (7, [11], other)]
    assert [g["week"] for g in games[11]] == [6, 7, 8]


def test_correct_boxes_starts_at_first_week(monkeypatch, other):
    _first_week(monkeypatch, other, 8)
    read = []

    def doc(lg, w):
        read.append(w)
        raise RuntimeError("stop")
    monkeypatch.setattr(espn_season, "_fetch_box_doc", doc)
    monkeypatch.setattr(espn_season, "kickoffs_by_week", lambda lg: {})
    lg = NS(current_week=10)
    out = espn_season.correct_boxes(lg, ref=other)
    assert read == [8, 9] and sorted(out["weeks"]) == [8, 9]
    read.clear()
    espn_season.correct_boxes(lg)
    assert read == list(range(1, 10))
