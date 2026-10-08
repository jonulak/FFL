import os
import shutil

import pytest

# The API's lifespan starts the live-scoring socket; a test run must never open it.
os.environ["FFL_LIVE"] = "0"
# Nothing in a test run may write to ESPN; espn_write swaps in an in-memory store.
os.environ["FFL_WRITES"] = "0"
# Instant free-agent adds are off by default until ffl-ut0.7 verifies them; the tests exercise that path.
os.environ["FFL_INSTANT_ADDS"] = "1"


@pytest.fixture(scope="session")
def _db_template(tmp_path_factory):
    """One migrated DB per test session (alembic upgrade head is ~0.5 s; per test it would dominate)."""
    from alembic import command
    from ffl import db
    path = tmp_path_factory.mktemp("db") / "template.db"
    mp = pytest.MonkeyPatch()
    mp.setenv("FFL_DB_URL", f"sqlite:///{path}")
    command.upgrade(db._alembic_config(), "head")
    mp.undo()
    return path


@pytest.fixture(autouse=True)
def _db_in_tmp(monkeypatch, tmp_path, _db_template):
    """Every test gets its own copy of the migrated DB; nothing reaches data/ffl.db."""
    from ffl import db
    target = tmp_path / "ffl.db"
    shutil.copy(_db_template, target)
    monkeypatch.setenv("FFL_DB_URL", f"sqlite:///{target}")
    db.reset()
    yield
    db.reset()


@pytest.fixture(autouse=True)
def _calibration_in_tmp(monkeypatch, tmp_path):
    """The pool builder snapshots every build for ffl.calibrate; a test build must not write
    into data/calibration."""
    from ffl import calibrate
    monkeypatch.setattr(calibrate, "CAL_DIR", tmp_path / "calibration")


@pytest.fixture(autouse=True)
def _no_claim_fact_reads(monkeypatch):
    """free_agent_pool and roster_state read waiver status and roster flags from ESPN; a test build never makes the call
    (a test that wants the facts patches pool_status / roster_flags, or raw_league_get itself)."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "raw_league_get", lambda *a, **k: {})
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg: {})


@pytest.fixture(autouse=True)
def _reconciled_reset(monkeypatch):
    """Every current-week box score fetch reconciles the transaction log (espn_season.reconcile_log);
    a test must not inherit another test's reconciled state."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "_RECONCILED", [None])


@pytest.fixture(autouse=True)
def _fresh_box_scores():
    """A finished week's box scores are kept for the process (espn_season.box_scores); one test's stub League
    must not answer another's."""
    from ffl import espn_season
    espn_season._BOXES.clear()
    yield
    espn_season._BOXES.clear()
