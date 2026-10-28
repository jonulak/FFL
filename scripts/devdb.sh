#!/bin/bash
# A development database on the local Postgres, and the .env line that points a checkout at it.
#
#   scripts/devdb.sh [name] [--from-dump FILE] [--from DB_URL] [--from-sqlite FILE] [--replace] [--no-env]
#
# Creates database ffl_<name> (name defaults to this worktree's directory name, `-` -> `_`) from
#   --from-dump FILE     a `pg_dump -Fc` file (pg_restore); s3://... is not supported yet (the bucket arrives in ffl-soc.6.1.8)
#   --from DB_URL        a live database, `pg_dump -Fc | pg_restore` (read-only on the source)
#   --from-sqlite FILE   the old SQLite file, `ffl.db pgcopy` (schema 0024)
#   (no source)          empty
# then `alembic upgrade head`, and writes FFL_DB_URL=postgresql+psycopg:///ffl_<name> into this checkout's .env,
# replacing that one line only (--no-env: print the URL and leave .env alone: a mock or bench database).
# An existing ffl_<name> is an error unless --replace. Local servers only: a PGHOST that is not this machine is refused.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# never in the main checkout (production's .env lives there): every worktree's git dir hangs off its .git
TREE="$(cd "$ROOT" && pwd -P)"
COMMON="$(git -C "$TREE" rev-parse --path-format=absolute --git-common-dir)"
MAIN_TREE="$(cd "$COMMON/.." && pwd -P)"
[ "$TREE" != "$MAIN_TREE" ] || { echo "devdb: $TREE is the main checkout (production runs here); run it from a worktree" >&2; exit 1; }
FORMULA=postgresql@18
BIN="$(brew --prefix "$FORMULA" 2>/dev/null)/bin"
[ -x "$BIN/psql" ] && export PATH="$BIN:$PATH"

NAME="" DUMP="" FROM="" SQLITE="" REPLACE=0 WRITE_ENV=1
while [ $# -gt 0 ]; do
  case "$1" in
    --from-dump) DUMP="${2:?--from-dump needs a file}"; shift 2 ;;
    --from) FROM="${2:?--from needs a database URL}"; shift 2 ;;
    --from-sqlite) SQLITE="${2:?--from-sqlite needs a file}"; shift 2 ;;
    --replace) REPLACE=1; shift ;;
    --no-env) WRITE_ENV=0; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) echo "devdb: unknown option $1" >&2; exit 2 ;;
    *) [ -z "$NAME" ] || { echo "devdb: one name only" >&2; exit 2; }; NAME="$1"; shift ;;
  esac
done

# --- the target must be this machine ---------------------------------------------------------------------------
HOST="${PGHOST:-}"
case "$HOST" in
  ""|localhost|127.0.0.1|::1|/*) ;;
  *) echo "devdb: PGHOST=$HOST is not a local server; this script only creates databases on this machine" >&2; exit 1 ;;
esac
if [ -n "$DUMP" ] && [ -n "$FROM" ] || [ -n "$DUMP" ] && [ -n "$SQLITE" ] || [ -n "$FROM" ] && [ -n "$SQLITE" ]; then
  echo "devdb: one source at most" >&2; exit 2
fi
case "$DUMP" in s3://*) echo "devdb: s3:// dumps are not supported yet (ffl-soc.6.1.8 creates the bucket); download it and pass the file" >&2; exit 2 ;; esac

# --- the name --------------------------------------------------------------------------------------------------
[ -n "$NAME" ] || NAME="$(basename "$ROOT")"
NAME="$(echo "$NAME" | tr '[:upper:]-' '[:lower:]_' | tr -cd 'a-z0-9_')"
DB="ffl_$NAME"
[ ${#DB} -le 63 ] || { echo "devdb: $DB is longer than Postgres's 63 characters" >&2; exit 2; }

# the URL written: the local socket (or the local TCP host PGHOST names), never anything else
case "$HOST" in
  "") TARGET="postgresql+psycopg:///$DB" ;;
  /*) TARGET="postgresql+psycopg:///$DB?host=$HOST" ;;
  *) TARGET="postgresql+psycopg://$HOST${PGPORT:+:$PGPORT}/$DB" ;;
esac

# --- create ----------------------------------------------------------------------------------------------------
psql -d postgres -Atqc "SELECT 1" >/dev/null || { echo "devdb: no local Postgres (scripts/pg-test-setup.sh starts it)" >&2; exit 1; }
if psql -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
  [ "$REPLACE" = 1 ] || { echo "devdb: $DB exists (--replace drops and remakes it)" >&2; exit 1; }
  dropdb --force "$DB"
fi
createdb "$DB"
# a failure from here on must not leave a half-built database behind
trap 'rc=$?; [ "$rc" -eq 0 ] || { dropdb --if-exists --force "$DB" 2>/dev/null; echo "devdb: failed (exit $rc); dropped $DB" >&2; }' EXIT

if [ -n "$DUMP" ]; then
  pg_restore --no-owner --no-acl -d "$DB" "$DUMP"
elif [ -n "$FROM" ]; then
  pg_dump -Fc --no-owner --no-acl "${FROM/postgresql+psycopg:/postgresql:}" | pg_restore --no-owner --no-acl -d "$DB"
fi

# the key ring secrets are sealed with (ffl-soc.3, ffl/vault.py): this checkout's own dev ring, made once into .env
# (--no-env too: it is not the database line). Rows restored from production's dump stay sealed under production's
# ring and read as not connected here.
ENVFILE="$ROOT/.env"
if [ -z "${FFL_SECRET_KEYS:-}" ] && ! { [ -f "$ENVFILE" ] && grep -Eq '^(export[[:space:]]+)?FFL_SECRET_KEYS=.' "$ENVFILE"; }; then
  RING="$(cd "$ROOT" && uv run python -m ffl.vault keygen)"
  printf 'FFL_SECRET_KEYS=%s\n' "$RING" >> "$ENVFILE"
  echo "devdb: a new dev key ring is in $ENVFILE (FFL_SECRET_KEYS)"
fi
[ -n "${FFL_SECRET_KEYS:-}" ] || FFL_SECRET_KEYS="$(sed -nE 's/^(export[[:space:]]+)?FFL_SECRET_KEYS=//p' "$ENVFILE" | tail -1)"
export FFL_SECRET_KEYS

export FFL_DB_URL="$TARGET"
(cd "$ROOT" && uv run alembic upgrade head)   # a restored dump older than the code migrates; a current one is a no-op
if [ -n "$SQLITE" ]; then
  (cd "$ROOT" && uv run python -m ffl.db pgcopy "$SQLITE")
fi

# --- .env ------------------------------------------------------------------------------------------------------
if [ "$WRITE_ENV" = 1 ]; then

  TMP="$(mktemp)"
  { [ -f "$ENVFILE" ] && grep -Ev '^(export[[:space:]]+)?FFL_DB_URL=' "$ENVFILE" || true; echo "FFL_DB_URL=$TARGET"; } > "$TMP"
  cat "$TMP" > "$ENVFILE"
  rm -f "$TMP"
  echo "devdb: $DB ready; $ENVFILE has FFL_DB_URL=$TARGET"
else
  echo "devdb: $DB ready; export FFL_DB_URL=$TARGET"
fi
