import json
from pathlib import Path
from types import SimpleNamespace

from espn_api.football.league import League

from ffl import espn_season

FX = json.loads((Path(__file__).parent / "fixtures" / "espn_box_week.json").read_text())


class _Req:
    def __init__(self):
        self.asked = []

    def league_get(self, params=None, headers=None, extend=""):
        self.asked.append((params, headers))
        return json.loads(json.dumps(FX["league_get"]))


def _lg():
    return SimpleNamespace(
        year=FX["year"], current_week=FX["current_week"], currentMatchupPeriod=FX["current_matchup_period"],
        settings=SimpleNamespace(matchup_periods=FX["matchup_periods"]),
        teams=[SimpleNamespace(team_id=i) for i in FX["team_ids"]], espn_request=_Req(),
        _get_pro_schedule=lambda sp: {int(k): tuple(v) for k, v in FX["pro_schedule"].items()},
        _get_positional_ratings=lambda sp: json.loads(json.dumps(FX["ratings"])))


def _flat(boxes):
    out = []
    for b in boxes:
        side = lambda t: getattr(t, "team_id", t)  # noqa: E731
        out.append((side(b.home_team), side(b.away_team), b.home_score, b.away_score, b.home_projected, b.away_projected,
                    [(p.playerId, p.slot_position, p.points, p.projected_points, p.proTeam, p.pro_opponent,
                      p.on_bye_week, getattr(p, "game_date", None), p.pro_pos_rank, p.injuryStatus)
                     for p in b.home_lineup + b.away_lineup]))
    return out


def test_a_stored_box_doc_parses_back_to_what_espn_api_builds():
    ref_lg, our_lg = _lg(), _lg()
    ref = League.box_scores(ref_lg, FX["week"])
    doc = json.loads(json.dumps(espn_season._fetch_box_doc(our_lg, FX["week"])))  # through storage, as the DB returns it
    assert _flat(espn_season._parse_box_doc(our_lg, doc)) == _flat(ref)
    assert our_lg.espn_request.asked == ref_lg.espn_request.asked, "the same ESPN request"
    assert any(not p.on_bye_week for b in ref for p in b.home_lineup), "fixture has players with games"


def test_box_hash_ignores_key_order():
    assert espn_season.box_hash({"a": 1, "b": [1, 2]}) == espn_season.box_hash({"b": [1, 2], "a": 1})
    assert espn_season.box_hash({"a": 1}) != espn_season.box_hash({"a": 2})


def test_a_week_after_the_current_one_is_asked_as_itself():
    """A league opened before its first week (ffl-soc.4.15): week 7 while ESPN is on 6 asks period 7 at scoring
    period 7; asking the current period answers sides without rosters, which do not parse."""
    lg = _lg()
    espn_season._fetch_box_doc(lg, FX["current_week"] + 1)
    params, headers = lg.espn_request.asked[-1]
    assert params["scoringPeriodId"] == FX["current_week"] + 1
    assert json.loads(headers["x-fantasy-filter"])["schedule"]["filterMatchupPeriodIds"]["value"] == ["7"]


def test_current_and_past_weeks_ask_as_before():
    for week in (FX["week"], FX["current_week"]):
        lg = _lg()
        espn_season._fetch_box_doc(lg, week)
        params, headers = lg.espn_request.asked[-1]
        assert params["scoringPeriodId"] == week
        assert json.loads(headers["x-fantasy-filter"])["schedule"]["filterMatchupPeriodIds"]["value"] == [str(week)]
