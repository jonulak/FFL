"""Pure league facts derived without the network."""
from datetime import date

from ffl.settings import nfl_week1_start


def test_week1_starts_the_tuesday_before_kickoff():
    assert nfl_week1_start(2024) == date(2024, 9, 3)
    assert nfl_week1_start(2025) == date(2025, 9, 2)   # Sep 1 is itself the first Monday
    assert nfl_week1_start(2026) == date(2026, 9, 8)
