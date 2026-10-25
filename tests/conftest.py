import os
import shutil

import pytest

# Deploy runs pytest from the main checkout: a leaked thread must fail loudly, not write to the production DB.
os.environ.setdefault("FFL_DB_URL", "sqlite:////nonexistent/ffl-test-guard.db")

# The API's lifespan starts the live-scoring socket; a test run must never open it.
os.environ["FFL_LIVE"] = "0"
# Nothing in a test run may write to ESPN; espn_write swaps in an in-memory store.
os.environ["FFL_WRITES"] = "0"
# Nothing in a test run may push to a phone; flush tests pass a fake sender.
os.environ["FFL_NOTIFY"] = "0"


@pytest.fixture(scope="session")
def _db_template(tmp_path_factory):
    """One migrated DB per test session (alembic upgrade head is ~0.5 s; per test it would dominate)."""
    from alembic import command
    from ffl import db
    path = tmp_path_factory.mktemp("db") / "template.db"
    mp = pytest.MonkeyPatch()
    mp.setenv("FFL_DB_URL", f"sqlite:///{path}")
    # migration 0014 seeds the home league from these, the last time league facts come from the environment
    mp.setenv("LEAGUE_ID", "100000001")
    mp.setenv("SEASON", "2026")
    mp.setenv("MY_TEAM_ID", "7")
    # migration 0015 imports <DATA>/settings.json when there is one (the main checkout's, where deploy runs the
    # tests): the template must not depend on which checkout runs it, so it sees an empty data dir; 0019 imports
    # the committed journal/draft/*.csv pick logs too, so it sees an empty journal as well
    from ffl import config
    mp.setattr(config, "DATA", tmp_path_factory.mktemp("data"))
    mp.setattr(config, "JOURNAL", tmp_path_factory.mktemp("journal"))
    command.upgrade(db._alembic_config(), "head")
    db.reset()
    db.engine().dispose()  # the connect hook switches the file to WAL, as production runs it; copies start there
    db.reset()
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
def _sources_reset():
    from ffl import consensus, sources
    sources.reset()
    consensus.forget()
    yield
    sources.reset()
    consensus.forget()


@pytest.fixture(autouse=True)
def _no_scraped_weekly_sources(monkeypatch):
    """A page build reads the CBS / FantasySharks / FleaFlicker weekly pages through espn_season.WEEKLY_SOURCES;
    no test reaches them (a test that wants them patches WEEKLY_SOURCES itself)."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "WEEKLY_SOURCES", {})
    monkeypatch.setattr(espn_season, "ROS_SOURCES", {})


@pytest.fixture(autouse=True)
def _no_claim_fact_reads(monkeypatch):
    """free_agent_pool and roster_state read waiver status and roster flags from ESPN; a test build never makes the call
    (a test that wants the facts patches pool_status / roster_flags, or raw_league_get itself)."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "raw_league_get", lambda *a, **k: {})
    monkeypatch.setattr(espn_season, "roster_flags", lambda lg, **k: {})


@pytest.fixture(autouse=True)
def _reconciled_reset(monkeypatch):
    """Every current-week box score fetch reconciles the transaction log (espn_season.reconcile_log);
    a test must not inherit another test's reconciled state."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "_RECONCILED", {})


@pytest.fixture(autouse=True)
def _league_builds_reset(monkeypatch):
    """fresh_league keeps each league's build time (espn_season._BUILT); one test's must not make the next's League look fresh."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "_BUILT", {})


@pytest.fixture(autouse=True)
def _gap_games_reset(monkeypatch):
    """A pickup's free-agent weeks are kept for the process (espn_season._gap_games); no test inherits another's."""
    from ffl import espn_season
    monkeypatch.setattr(espn_season, "_GAP_GAMES", {})


@pytest.fixture(autouse=True)
def _fresh_box_scores():
    """A finished week's box scores are kept for the process (espn_season.box_scores); one test's stub League
    must not answer another's."""
    from ffl import espn_season
    espn_season._BOXES.clear()
    yield
    espn_season._BOXES.clear()


@pytest.fixture(autouse=True)
def _empty_api_cache():
    """The API's page cache is process-wide, and a logged move rebuilds every page in it (api.on_new_rows): a test
    that syncs a move must not rebuild pages another test cached with builders that no longer stub ESPN."""
    import sys
    api = sys.modules.get("ffl.api")
    if api is not None:
        api.cache.clear()
    yield
    api = sys.modules.get("ffl.api")
    if api is not None:
        api.cache.join()
        with api.cache._lock:                            # memory only: the test's DB is a throwaway copy
            api.cache._d.clear()
