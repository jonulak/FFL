import itertools
import os

import pytest

# Deploy runs pytest from the main checkout: a leaked thread must fail loudly (and fast), not write to the production
# DB. Every test points FFL_DB_URL at the session's test database (below); this is only what is left outside one.
os.environ["FFL_DB_URL"] = "postgresql+psycopg://nobody@127.0.0.1:1/none?connect_timeout=2"

# The server the test databases are made on: a role that may CREATE DATABASE (scripts/pg-test-setup.sh; CI sets it
# to its postgres:16 service). Never the production server's address.
TEST_PG = os.environ.get("FFL_TEST_PG", "postgresql+psycopg:///postgres")

# The API's lifespan starts the live-scoring socket; a test run must never open it.
os.environ["FFL_LIVE"] = "0"
# Nothing in a test run may write to ESPN; espn_write swaps in an in-memory store.
os.environ["FFL_WRITES"] = "0"
# Nothing in a test run may push to a phone; flush tests pass a fake sender.
os.environ["FFL_NOTIFY"] = "0"
# The ring secrets are sealed with (ffl/vault.py): one fresh key per run, never a real one.
os.environ["FFL_SECRET_KEYS"] = "test:" + __import__("base64").b64encode(os.urandom(32)).decode()


class PgServer:
    """CREATE / DROP DATABASE on the test server, from an AUTOCOMMIT connection (neither runs in a transaction)."""

    def __init__(self, admin_url: str):
        import sqlalchemy as sa
        self.admin = sa.engine.make_url(admin_url)
        self.engine = sa.create_engine(self.admin, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
        self._n = itertools.count(1)

    def url(self, name: str) -> str:
        return self.admin.set(database=name).render_as_string(hide_password=False)

    def create(self, name: str, template: str | None = None) -> str:
        with self.engine.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            c.exec_driver_sql(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else ""))
        return self.url(name)

    def drop(self, name: str) -> None:
        with self.engine.connect() as c:
            c.exec_driver_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # FORCE: a leaked thread's connection

    def fresh_name(self) -> str:
        return f"ffl_t_{os.getpid()}_{next(self._n)}"


@pytest.fixture(scope="session")
def pg_server():
    server = PgServer(TEST_PG)
    yield server
    server.engine.dispose()


@pytest.fixture
def empty_db(pg_server, monkeypatch):
    """A function that makes an empty database (no schema) and returns its URL; each is dropped after the test.
    For tests that run migrations themselves (the template is already at head)."""
    from ffl import db
    made = []

    def make() -> str:
        name = pg_server.fresh_name()
        made.append(name)
        return pg_server.create(name)
    yield make
    db.reset()
    for name in made:
        pg_server.drop(name)


@pytest.fixture(scope="session")
def _db_template(tmp_path_factory, pg_server):
    """One migrated DB per test session (alembic upgrade head takes seconds; per test it would dominate): the
    working DB below is a `CREATE DATABASE ... TEMPLATE` copy of it, as is a test's own second database."""
    from alembic import command
    from ffl import db
    name = f"ffl_tpl_{os.getpid()}"
    mp = pytest.MonkeyPatch()
    mp.setenv("FFL_DB_URL", pg_server.create(name))
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
    # the signed-in test user (TEST_USER, below) owns the home league, as Jonathan's user does in production
    import sqlalchemy as sa
    with db._write() as c:
        c.execute(sa.insert(db.users).values(id=TEST_USER["id"], email=TEST_USER["email"], name=TEST_USER["name"],
                                             created_at="2026-10-27T00:00:00+00:00"))
        c.execute(sa.insert(db.memberships).values(user_id=TEST_USER["id"], league="espn:100000001", team_id=7,
                                                   is_default=True, created_at="2026-10-27T00:00:00+00:00"))
    db.reset()  # a template takes no copies while anything is connected to it
    mp.undo()
    yield name
    db.reset()
    pg_server.drop(name)


_SEED = "ffl_seed"  # the schema, in the working DB, holding the template's own rows (the migrations' seeds)


@pytest.fixture(scope="session")
def _db_work(pg_server, _db_template):
    """The session's working DB, a copy of the template, and the SQL that puts it back as the template left it.

    A `CREATE DATABASE ... TEMPLATE` per test made the suite ~7x slower than SQLite's file copies (179 s against
    24 s), so tests share this one database, emptied before each test: every table's rows deleted (children first;
    DELETE, not TRUNCATE, which costs ~20 ms a test in new files) and every id sequence restarted, then the
    template's rows (the home league, strategies, formats the migrations seed) copied back from `ffl_seed`."""
    import sqlalchemy as sa
    from ffl import db
    name = f"ffl_w_{os.getpid()}"
    url = pg_server.create(name, template=_db_template)
    eng = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with eng.begin() as c:
        ins = sa.inspect(c)
        have = set(ins.get_table_names()) - {"alembic_version"}
        assert have == set(db.metadata.tables), "the template is at head: db.metadata names every table"
        tables = [t.name for t in db.metadata.sorted_tables]           # parents first
        seeded = [t for t in tables if c.exec_driver_sql(f'SELECT EXISTS (SELECT 1 FROM "{t}")').scalar()]
        c.exec_driver_sql(f"CREATE SCHEMA {_SEED}")
        sql = [f'DELETE FROM public."{t}"' for t in reversed(tables)]
        for t in seeded:
            c.exec_driver_sql(f'CREATE TABLE {_SEED}."{t}" AS SELECT * FROM public."{t}"')
            sql.append(f'INSERT INTO public."{t}" SELECT * FROM {_SEED}."{t}"')
        for t in tables:
            for col in ins.get_columns(t):
                seq = c.exec_driver_sql(f"SELECT pg_get_serial_sequence('public.\"{t}\"', '{col['name']}')").scalar()
                if seq:  # as a fresh copy has it: next id 1, or past a seeded row's
                    sql.append(f"SELECT setval('{seq}', COALESCE((SELECT MAX(\"{col['name']}\") FROM public.\"{t}\"), 0)"
                               " + 1, false)")
    yield url, eng, ";\n".join(sql)                    # eng: NullPool, so it holds no connection between tests
    eng.dispose()
    pg_server.drop(name)


def _reset_db(eng, sql: str) -> None:
    """Back to the template's rows. Any other session still on the DB (a thread a test leaked) is ended first:
    the DELETEs would wait on its locks, and it must not write into the next test."""
    with eng.begin() as c:
        c.exec_driver_sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "   # clients only: not autovacuum
                          "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                          "AND backend_type = 'client backend'")
        c.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        c.exec_driver_sql(sql)


@pytest.fixture(autouse=True)
def _db_in_tmp(monkeypatch, _db_work):
    """Every test starts on the migrated DB as the template left it; nothing reaches the production DB."""
    from ffl import db
    url, eng, sql = _db_work
    db.reset()
    _reset_db(eng, sql)
    monkeypatch.setenv("FFL_DB_URL", url)
    yield
    db.reset()


TEST_USER = {"id": 0, "email": "test@example.com", "name": "Test"}


@pytest.fixture(autouse=True)
def _signed_in(request, monkeypatch):
    """Every API request passes as TEST_USER (job routes too), as before sign-in existed; a test marked `real_auth`
    runs the real middleware (tests/test_auth_routes.py)."""
    if request.node.get_closest_marker("real_auth"):
        return
    from ffl import auth
    monkeypatch.setattr(auth, "decide", lambda *a, **k: (0, TEST_USER))


@pytest.fixture(autouse=True)
def _claude_token(monkeypatch):
    """Every stubbed `claude -p` call has a token (the environment's), and no test inherits another's cached row."""
    from ffl import claude_cli
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-test")
    claude_cli.forget_token()
    yield
    claude_cli.forget_token()


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
    must not answer another's, nor the pool's league half built on them (espn_season._POOL)."""
    from ffl import espn_season
    espn_season._BOXES.clear()
    espn_season._POOL.clear()
    yield
    espn_season._BOXES.clear()
    espn_season._POOL.clear()


@pytest.fixture(autouse=True)
def _empty_api_caches():
    """The API's page caches (one per member and league, api.caches) are process-wide, and a logged move rebuilds
    every page in them (api.on_new_rows): each test starts with none, and what a test made is joined and dropped after
    it (memory only: the test's DB is a throwaway copy), so no test rebuilds pages another cached with builders that
    no longer stub ESPN."""
    import sys
    api = sys.modules.get("ffl.api")
    if api is not None:
        with api._CACHES:
            api.caches.clear()
    yield
    api = sys.modules.get("ffl.api")
    if api is not None:
        with api._CACHES:
            made = list(api.caches.values())
            api.caches.clear()
        for c in made:
            c.join()
            with c._lock:
                c._d.clear()


@pytest.fixture(autouse=True)
def _acting_user(monkeypatch):
    """The CLI acts as TEST_USER (FFL_USER), whose default is the home league."""
    monkeypatch.setenv("FFL_USER", TEST_USER["email"])
    monkeypatch.delenv("FFL_LEAGUE", raising=False)


@pytest.fixture(autouse=True)
def _write_fakes_reset():
    """The per-league fake write stores and kept pending reads start empty in every test."""
    from ffl import espn_write
    espn_write.reset_fakes()
    yield
    espn_write.reset_fakes()


@pytest.fixture
def as_job(monkeypatch):
    """Requests pass as a job's (the timers' token: no user), not TEST_USER's: the routes a job runs with no league
    fan out over every league."""
    from ffl import auth
    monkeypatch.setattr(auth, "decide", lambda *a, **k: (0, None))
