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
