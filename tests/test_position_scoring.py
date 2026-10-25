"""Per-position scoring in rescoring and live points (ffl-soc.2, ffl-twi): a TE premium or a D/ST override
reaches only its own position, and the home league's numbers are what the flat list gave."""
import copy
import json
from pathlib import Path

import pytest

from ffl import fastcast as fc
from ffl import formats, leagues, live, settings, sleeper
from ffl.sources import scoring_key

FIX = Path(__file__).parent / "fixtures"
HOME_ITEMS = json.loads((FIX / "home_scoring_items.json").read_text())   # ESPN's raw items, D/ST overrides kept
CHECKPOINT = json.loads((FIX / "fastcast_checkpoint.json").read_text())


def home_settings() -> dict:
    return json.loads((FIX / "home_settings.json").read_text())


def te_premium(items: list[dict]) -> list[dict]:
    """The home items with a TE premium: 1.5 a catch on TE's slot (6)."""
    out = copy.deepcopy(items)
    rec = next(i for i in out if i["statId"] == 53)
    rec["pointsOverrides"] = {**(rec.get("pointsOverrides") or {}), "6": 1.5}
    return out


def test_te_premium_reaches_rescoring_at_te_only():
    formats.store(leagues.home(), {**home_settings(), "scoring_items": te_premium(HOME_ITEMS)})
    ks = settings.rescoring(settings.load())
    assert ks["TE"]["rec"] == 1.5 and ks["WR"]["rec"] == ks["RB"]["rec"] == ks["QB"]["rec"] == 1.0
    raw = [{"player": {"position": p, "first_name": n, "last_name": "X", "team": "KC"},
            "stats": {"gp": 17, "rec": 80, "rec_yd": 800}} for p, n in (("TE", "Tight"), ("WR", "Wide"))]
    got = sleeper.rescore(raw, ks).set_index("pos")["proj_sleeper"]
    assert got["TE"] == 200.0 and got["WR"] == 160.0


def test_a_te_premium_is_its_own_rescored_cache_key():
    plain = settings.rescoring({"scoring_tables": formats.scoring_from_espn(HOME_ITEMS)[0]})
    prem = settings.rescoring({"scoring_tables": formats.scoring_from_espn(te_premium(HOME_ITEMS))[0]})
    assert scoring_key(plain) != scoring_key(prem)


def test_rescoring_without_tables_reads_the_flat_list():
    s = home_settings()
    assert "scoring_tables" not in s
    ks = settings.rescoring(s)
    assert set(ks) == {"QB", "RB", "WR", "TE"} and ks["QB"]["pass_int"] == -2.0 and ks["QB"]["pass_2pt"] == 2.0


def test_live_te_premium_scores_a_te_and_not_a_wr():
    table = fc.scoring_table({"scoring_items": te_premium(HOME_ITEMS)})
    doc = {"statistics": {"1": {"positionId": "4", "stats": {"53": 5.0}}, "2": {"positionId": "3", "stats": {"53": 5.0}}}}
    assert fc.score_all(doc, table) == {1: 7.5, 2: 5.0}


def test_a_dst_override_never_scores_an_offensive_player():
    table = fc.scoring_table({"scoring_items": [{"statId": 89, "points": 0.0, "pointsOverrides": {"16": 5.0}}]})
    doc = {"statistics": {"-16004": {"stats": {"89": 1.0}}, "7": {"positionId": "3", "stats": {"89": 1.0}}}}
    assert fc.score_all(doc, table) == {-16004: 5.0, 7: 0.0}, "a D/ST by its negative id; the WR takes base points"


def test_home_raw_items_score_the_checkpoint_as_the_flat_list_did():
    flat = fc.score_all(CHECKPOINT, fc.scoring_table(home_settings()))
    per = fc.score_all(CHECKPOINT, fc.scoring_table({**home_settings(), "scoring_items": HOME_ITEMS}))
    assert per == flat


def test_box_rates_are_his_positions_and_sum_to_his_points():
    s = {**home_settings(), "scoring_items": te_premium(HOME_ITEMS)}
    items, table = live.box_items(s), fc.scoring_table(s)
    row = {"53": 6.0, "42": 70.0, "43": 1.0}
    for pos, pid in (("TE", 1), ("WR", 2)):
        box = live.box_score(row, items, pos)
        want = fc.score_all({"statistics": {str(pid): {"positionId": {"TE": "4", "WR": "3"}[pos], "stats": row}}}, table)[pid]
        assert round(sum(b["points"] for b in box), 2) == want
    assert next(b for b in live.box_score(row, items, "TE") if b["stat_id"] == 53)["rate"] == 1.5


def test_box_items_add_a_stat_that_scores_only_at_one_position():
    s = {**home_settings(), "scoring_items": [*HOME_ITEMS, {"statId": 58, "points": 0.0, "pointsOverrides": {"6": 0.5}}]}
    extra = next(x for x in live.box_items(s) if x["stat_id"] == 58)   # targets, at TE only
    assert extra["by_pos"]["TE"] == 0.5 and extra["by_pos"]["WR"] == 0.0 and extra["label"]


@pytest.mark.parametrize("pos", ["QB", "RB", "WR", "TE"])
def test_home_rescoring_is_the_flat_list_at_every_position(pos):
    stored = settings.rescoring({"scoring_tables": formats.scoring_from_espn(HOME_ITEMS)[0]})
    assert stored[pos] == settings.rescoring(home_settings())[pos]
