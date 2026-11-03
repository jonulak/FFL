#!/bin/bash
# One-time (and idempotent) local setup for the test suite on Postgres: the Homebrew server running, and a role the
# tests may create and drop databases with. The suite's default admin URL is the socket as you
# (postgresql+psycopg:///postgres, a superuser on a Homebrew install); to run it as the role instead:
#   FFL_TEST_PG=postgresql+psycopg://ffl_test_admin@/postgres uv run pytest -q
# CI does not run this: its pgvector/pgvector:pg16-trixie service container is the server (.github/workflows/test.yml).
set -euo pipefail

FORMULA=postgresql@18      # local; the stack runs postgres:16, so nothing may use a PG 17+ feature

brew list pgvector >/dev/null 2>&1 || brew install pgvector   # migration 0038's vector type
if ! brew services list | grep -E "^$FORMULA +started" >/dev/null; then
  brew services start "$FORMULA"
fi
PSQL="$(brew --prefix "$FORMULA")/bin/psql"
for _ in $(seq 1 30); do
  "$PSQL" -d postgres -Atqc "SELECT 1" >/dev/null 2>&1 && break
  sleep 1
done
"$PSQL" -d postgres -Atqc "SELECT 1" >/dev/null

"$PSQL" -d postgres -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ffl_test_admin') THEN
    CREATE ROLE ffl_test_admin LOGIN CREATEDB;
  END IF;
END $$;
SQL
# pgvector is not a trusted extension: only a superuser creates it. In template1, every new database (so the role's
# too) starts with it, and 0038's CREATE EXTENSION IF NOT EXISTS has nothing to do.
"$PSQL" -d template1 -v ON_ERROR_STOP=1 -qc "CREATE EXTENSION IF NOT EXISTS vector"
echo "pg-test-setup: $FORMULA running with pgvector; role ffl_test_admin (LOGIN CREATEDB) present"
