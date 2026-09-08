#!/bin/sh
# Regenerate web/lib/api.d.ts from the FastAPI schema. Run from anywhere in the repo.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
uv run python -c "import json; from ffl.api import app; print(json.dumps(app.openapi()))" > web/openapi.json
cd web && pnpm exec openapi-typescript openapi.json -o lib/api.d.ts && rm -f openapi.json
