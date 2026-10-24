"""Regression tests for replacement level / VORP in ffl.board."""
import pandas as pd
import pytest

from ffl import board as board_mod

SETTINGS = {
    "team_count": 10,
    "starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "RB/WR/TE": 1},
    "roster_size": 16,
    "name": "test",
    "season": 2026,
}


# roughly the real shape of each position: kickers and defenses live on a much flatter,
# lower scale than skill players, which is what makes their replacement level cheap
SCALE = {"QB": (360.0, 4.0), "RB": (350.0, 4.0), "WR": (340.0, 4.0),
         "TE": (250.0, 3.0), "D/ST": (120.0, 1.0), "K": (150.0, 0.8)}


def make_players(n_per_pos: int = 60) -> pd.DataFrame:
    rows = []
    pid = 1
    for pos, (top, step) in SCALE.items():
        for i in range(n_per_pos):
            rows.append({"player_id": pid, "name": f"{pos}{i}", "pos": pos, "team": f"T{i}",
                         "proj": top - i * step, "injury": "ACTIVE"})
            pid += 1
    return pd.DataFrame(rows)


EMPTY_ADP = pd.DataFrame(columns=["key", "pos", "team", "adp", "adp_rank", "adp_pos_rank",
                                  "adp_1qb", "adp_1qb_rank", "bye"])


def test_top_available_stays_above_replacement_midraft():
    """The best available player at a position must keep positive VORP after a run on it.

    Regression: replacement level dropped BENCH_DEPTH mid-draft, so once a position ran hot
    the replacement index hit 0 — replacement became the best available player, zeroing out
    every VORP at that position and erasing it from recommendations.
    """
    players = make_players()
    wrs = players[players.pos == "WR"].sort_values("proj", ascending=False)
    drafted = set(wrs.head(24).player_id)  # a heavy run on WR

    bd = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=drafted)
    best_wr = bd[(bd.pos == "WR") & (~bd.drafted)].nlargest(1, "proj").iloc[0]
    assert best_wr.vorp > 0, f"best available WR has VORP {best_wr.vorp}; replacement level collapsed"


@pytest.mark.parametrize("pos", ["QB", "RB", "WR", "TE"])
def test_replacement_falls_as_a_position_is_drafted(pos):
    """Taking players off the board should raise, never lower, what is left worth."""
    players = make_players()
    sub = players[players.pos == pos].sort_values("proj", ascending=False)
    drafted = set(sub.head(15).player_id)

    pre = board_mod.build(SETTINGS, players, EMPTY_ADP)
    post = board_mod.build(SETTINGS, players, EMPTY_ADP, drafted_ids=drafted)
    assert post[post.pos == pos].repl.iloc[0] <= pre[pre.pos == pos].repl.iloc[0]


def test_predraft_replacement_uses_starters_plus_bench():
    players = make_players()
    bd = board_mod.build(SETTINGS, players, EMPTY_ADP)
    counts = board_mod.starter_counts(SETTINGS)
    import math
    for pos in ["RB", "WR", "TE"]:
        want = math.ceil(counts[pos] + board_mod.bench_depth(SETTINGS)[pos]) - 1
        pool = players[players.pos == pos].sort_values("proj", ascending=False).proj.values
        assert bd[bd.pos == pos].repl.iloc[0] == pytest.approx(pool[want])


# --- draft inputs in the DB (ffl-soc) ---------------------------------------------------------------

import io
import json
from pathlib import Path

from ffl import adp, config, db, formats, leagues, sleeper
from ffl import players as players_mod
from ffl import settings as settings_mod
from ffl.leagues import LeagueRef

FX = Path(__file__).parent / "fixtures" / "home_settings.json"   # 10 teams, 2QB, 1 PPR, 4-pt pass TD, 2026


def home_fx() -> dict:
    return json.loads(FX.read_text())


def register(n: int, doc: dict) -> LeagueRef:
    ref = leagues.register(LeagueRef(f"espn:{n}", "espn", str(n), 2026, f"league {n}", None, False, None, None))
    return formats.store(ref, {**doc, "league_id": n})


def espn_entry(pid: int, name: str, proj: float) -> dict:
    return {"onTeamId": 0, "status": "FREEAGENT",
            "player": {"id": pid, "fullName": name, "proTeamId": 2, "injuryStatus": "ACTIVE",
                       "stats": [{"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 1, "appliedTotal": proj}],
                       "ownership": {"averageDraftPosition": 10.0, "auctionValueAverage": 5.0},
                       "draftRanksByRankType": {"PPR": {"rank": 3}, "STANDARD": {"rank": 4}}}}


@pytest.fixture
def espn(monkeypatch):
    """ESPN's kona_player_info, counted: two players per position, ids by position."""
    calls = []

    def fetch(pos, limit, season, ref=None):
        calls.append((pos, ref.key if ref else None))
        base = 1000 * (players_mod.POSITIONS.index(pos) + 1)
        return {"players": [espn_entry(base + 1, f"{pos} One", 200.0), espn_entry(base + 2, f"{pos} Two", 100.0)]}

    monkeypatch.setattr(players_mod, "_fetch_position", fetch)
    return calls


def test_pool_shared_by_format(espn):
    home = formats.store(leagues.home(), home_fx())
    other = register(999, home_fx())
    assert players_mod.store_pool(home) == players_mod.POSITIONS
    assert len(espn) == 6
    assert players_mod.store_pool(other) == [], "a league on the same six formats fetches nothing"
    assert len(espn) == 6
    pd.testing.assert_frame_equal(players_mod.pool(other), players_mod.pool(home))

    # a TE premium: the reception is worth 1.5 to a TE alone, so only TE's format is new
    tep = home_fx()
    tep["scoring_items"] = [{"statId": x["stat_id"], "points": x["points"],
                             **({"pointsOverrides": {"6": 1.5}} if x["abbr"] == "REC" else {})}
                            for x in tep["scoring"]]
    te_league = register(998, tep)
    assert players_mod.store_pool(te_league) == ["TE"]
    assert espn[6:] == [("TE", "espn:998")], "fetched through the league that needs it"

    assert players_mod.store_pool(home, refresh=True) == players_mod.POSITIONS and len(espn) == 13


def test_pool_is_the_espn_players_frame(espn):
    home = formats.store(leagues.home(), home_fx())
    players_mod.store_pool(home)
    df = players_mod.pool(home)
    assert list(df.columns) == players_mod.COLUMNS
    assert list(df.proj) == sorted(df.proj, reverse=True)
    assert list(df.index) == list(range(len(df)))
    assert df.player_id.is_unique and len(df) == 12
    # tied projections keep position order (a stable sort): the board's ranks depend on it
    assert list(df[df.proj == 200.0].pos) == players_mod.POSITIONS


def test_pool_missing_says_how_to_fetch_it():
    home = formats.store(leagues.home(), home_fx())
    with pytest.raises(SystemExit, match="ffl.players"):
        players_mod.pool(home)


QB_RAW = [{"player": {"position": "QB", "first_name": "Josh", "last_name": "Allen", "team": "BUF"},
           "stats": {"gp": 17.0, "pass_yd": 4000.0, "pass_td": 30.0, "pass_int": 10.0, "rush_yd": 500.0,
                     "rush_td": 8.0, "fum_lost": 2.0, "adp_2qb": 2.1, "adp_ppr": 20.0}},
          {"player": {"position": "DEF", "team": "JAC"}, "stats": {"gp": 17.0, "pts_std": 120.0}}]


def with_pass_td(points: float) -> dict:
    doc = home_fx()
    doc["scoring"] = [{**x, "points": points} if x["abbr"] == "PTD" else x for x in doc["scoring"]]
    return doc


def test_sleeper_rescored_per_format(monkeypatch):
    monkeypatch.setattr(sleeper, "fetch_raw", lambda season: pytest.fail("the stored raw is read, not fetched"))
    four = formats.store(leagues.home(), with_pass_td(4))
    six = register(997, with_pass_td(6))
    db.put_sleeper_season(2026, QB_RAW)
    a, b = sleeper.season_players(four).set_index("key"), sleeper.season_players(six).set_index("key")
    assert b.proj_sleeper["josh allen"] - a.proj_sleeper["josh allen"] == pytest.approx(2 * 30)
    assert a.proj_sleeper["jaguars d/st"] == b.proj_sleeper["jaguars d/st"] == 120.0


def test_sleeper_raw_fetched_once_on_first_use(monkeypatch):
    home = formats.store(leagues.home(), home_fx())
    fetched = []
    monkeypatch.setattr(sleeper, "fetch_raw", lambda season: fetched.append(season) or QB_RAW)
    sleeper.season_players(home)
    sleeper.season_players(home)
    assert fetched == [2026]
    assert db.sleeper_season_raw(2026) == QB_RAW


def test_season_players_is_the_old_csv_frame():
    """The stored raw, rescored, is the frame sleeper_players.csv held: the old fetch written and read back."""
    home = formats.store(leagues.home(), home_fx())
    db.put_sleeper_season(2026, QB_RAW)
    old = sleeper.rescore(QB_RAW, settings_mod.key_scoring(settings_mod.load(home)["scoring"]))
    want = pd.read_csv(io.StringIO(old.to_csv(index=False)))
    pd.testing.assert_frame_equal(sleeper.season_players(home), want)
    assert list(want.columns) == ["name", "key", "pos", "team", "proj_sleeper", "sleeper_gp",
                                  "adp_2qb_sleeper", "adp_ppr_sleeper"]


def test_board_markdown_names_the_feed(monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    s = {**SETTINGS, "bench_size": 7, "scoring": [{"stat_id": 53, "abbr": "REC", "label": "", "points": 1.0}]}
    bd = board_mod.build(s, make_players(), EMPTY_ADP)
    for c in ("proj_espn", "proj_sleeper"):
        bd[c] = bd.proj
    text = board_mod.to_markdown(bd, s)
    assert "1QB" in text and "2QB drafts" not in text and "2QB ADP" not in text
    two = {**s, "starters": {**s["starters"], "QB": 2}}
    assert "2QB drafts" in board_mod.to_markdown(bd, two)


def ffc_frame() -> pd.DataFrame:
    df = pd.DataFrame({"name": ["QB One", "RB One"], "key": ["qb one", "rb one"], "pos": ["QB", "RB"],
                       "team": ["BUF", "BUF"], "bye": [7, 7], "adp": [3.0, 1.0], "adp_stdev": [1.0, 1.0],
                       "adp_rank": [2, 1], "adp_pos_rank": [1, 1]})
    df.attrs["meta"] = {}
    return df


def test_load_inputs_from_the_db(espn, monkeypatch):
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    home = formats.store(leagues.home(), home_fx())
    players_mod.store_pool(home)
    db.put_sleeper_season(2026, QB_RAW)
    db.put_adp_feed("2qb", 10, 2026, adp.records(ffc_frame()), {})
    db.put_adp_feed("ppr", 10, 2026, adp.records(ffc_frame()), {})
    s, pl, a = board_mod.load_inputs(home)
    assert s["league_key"] == home.key
    assert {"proj_espn", "proj_sleeper", "proj_sources"} <= set(pl.columns)
    assert "adp_1qb" in a.columns
    assert board_mod.build(s, pl, a).set_index("name").adp["QB One"] == 3.0


def test_no_files_written(espn, monkeypatch, tmp_path, capsys):
    """settings, players, sleeper, adp, board and avail keep everything in the DB: data/ stays empty."""
    from ffl import avail
    monkeypatch.delenv("FFL_ADP_MODE", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(avail, "DATA", data)
    monkeypatch.setattr(settings_mod, "fetch", lambda ref: {**home_fx(), "first_week": 1})
    monkeypatch.setattr(sleeper, "fetch_raw", lambda season: QB_RAW)
    monkeypatch.setattr(adp, "fetch", lambda fmt, teams, year: ffc_frame())
    settings_mod.main([])
    players_mod.main([])
    sleeper.main([])
    adp.main([])
    board_mod.main([])
    avail.main("QB")
    out = capsys.readouterr().out
    assert "# Draft board:" in out and "QB One" in out
    assert list(data.iterdir()) == []
