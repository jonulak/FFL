"""Pure league facts derived without the network."""
from datetime import date

from ffl.settings import nfl_week1_start, playoff_weeks


def test_week1_starts_the_tuesday_before_kickoff():
    assert nfl_week1_start(2024) == date(2024, 9, 3)
    assert nfl_week1_start(2025) == date(2025, 9, 2)   # Sep 1 is itself the first Monday
    assert nfl_week1_start(2026) == date(2026, 9, 8)


def test_playoff_weeks_are_the_matchup_periods_after_the_regular_season():
    # scheduleSettings as ESPN serves this league: 14 regular-season matchups, then three one-week rounds
    sched = {"matchupPeriodCount": 14, "matchupPeriods": {str(i): [i] for i in range(1, 18)}}
    assert playoff_weeks(sched) == [15, 16, 17]
    # a two-week final is one matchup period over two NFL weeks
    two = {"matchupPeriodCount": 13, "matchupPeriods": {**{str(i): [i] for i in range(1, 16)}, "16": [16, 17]}}
    assert playoff_weeks(two) == [14, 15, 16, 17]


def test_main_stores_and_prints_and_writes_no_file(monkeypatch, tmp_path, capsys):
    import json
    from pathlib import Path
    from ffl import config, settings
    fx = json.loads((Path(__file__).parent / "fixtures" / "home_settings.json").read_text())
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(config, "DATA", data)
    monkeypatch.setattr(settings, "fetch", lambda ref: {**fx, "first_week": 1})
    settings.main([])
    out = capsys.readouterr().out
    assert "# 2026 Fantasy Football" in out and "Key scoring:" in out
    assert list(data.iterdir()) == [], "settings live in the DB: no settings.json, no settings.md"
    assert settings.load() == {**fx, "first_week": 1, "league_key": "espn:100000001"}


def test_fetch_reads_first_week_from_espn_status(monkeypatch):
    from ffl import leagues, settings
    raw = {"settings": {"name": "x", "size": 2,
                        "scoringSettings": {"scoringItems": [
                            {"statId": 53, "points": 1.0, "pointsOverrides": {"6": 1.5}},
                            {"statId": 89, "points": 0.0, "pointsOverrides": {"16": 5.0}}]},
                        "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 4},
                        "rosterSettings": {"lineupSlotCounts": {"0": 1, "20": 2, "21": 1}, "positionLimits": {"1": 4}},
                        "draftSettings": {}, "acquisitionSettings": {}},
           "teams": [], "members": []}
    calls = []

    def get(view, *a, ref=None, **k):
        calls.append(view)
        return raw if view == ["mSettings", "mTeam"] else {"status": {"firstScoringPeriod": 3}}
    monkeypatch.setattr(settings, "raw_league_get", get)
    d = settings.fetch(leagues.home())
    assert d["first_week"] == 3 and calls == [["mSettings", "mTeam"], ["mStatus"]]
    assert d["scoring_items"][0]["pointsOverrides"] == {"6": 1.5}, "the raw items, overrides and all"
    assert {x["abbr"]: x["points"] for x in d["scoring"]} == {"REC": 1.0, "PA0": 5.0}, "today's flat list"
    assert d["league_id"] == 100000001 and d["position_limits"] == {"QB": 4}
