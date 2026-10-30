"""The API's memory (#472): pandas stays off its import path, and Sleeper's cached rows keep only what is read."""
import subprocess
import sys

from ffl import espn_season


def test_the_api_imports_without_pandas():
    """pandas and numpy are ~170 MB of the API's RSS; only the draft tools need them."""
    out = subprocess.run([sys.executable, "-c", "import sys, ffl.api; print('pandas' in sys.modules, 'numpy' in sys.modules)"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["False", "False"]


def test_slim_sleeper_keeps_what_the_parsers_read():
    raw = [{"team": "PHI", "status": "x", "metadata": {"big": 1}, "stats": {"gp": 1, "pass_yd": 250.0},
            "player": {"first_name": "Jalen", "last_name": "Hurts", "position": "QB", "team": "PHI",
                       "years_exp": 5, "injury_notes": "n", "metadata": {"rookie": False}}},
           {"team": "DAL", "player": {"position": "DEF", "team": "DAL"}}]
    rows = espn_season.slim_sleeper(raw)
    assert rows[0] == {"team": "PHI", "stats": {"gp": 1, "pass_yd": 250.0},
                       "player": {"first_name": "Jalen", "last_name": "Hurts", "position": "QB", "team": "PHI", "years_exp": 5}}
    assert rows[1] == {"team": "DAL", "stats": {}, "player": {"position": "DEF", "team": "DAL"}}, "a missing key stays missing"
    ks = {"QB": {"pass_yd": 0.04}}
    assert espn_season.parse_sleeper_rows(rows, ks) == espn_season.parse_sleeper_rows(raw, ks)
    assert espn_season.parse_sleeper_exp(rows) == espn_season.parse_sleeper_exp(raw)
