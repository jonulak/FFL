#!/bin/sh
# The API container: migrate, then serve.
set -e
cd /app
uv run --no-sync alembic upgrade head
exec uv run --no-sync uvicorn ffl.api:app --host 0.0.0.0 --port 8300
