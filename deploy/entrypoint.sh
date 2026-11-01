#!/bin/sh
# The API container: migrate, then serve.
set -e
cd /app
uv run --no-sync alembic upgrade head
uv run --no-sync python -m ffl.notify ensure-keys  # push keys live in the database: made once, kept across upgrades
exec uv run --no-sync uvicorn ffl.api:app --host 0.0.0.0 --port 8300
