"""deploy/env.example names every FFL_ setting the app reads, bar the dev and CLI ones."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEV_ONLY = {
    "FFL_ADP_MODE", "FFL_CDP_PORT", "FFL_INSTANT_ADDS", "FFL_LEAGUE", "FFL_LIVE", "FFL_NOTIFY",
    "FFL_PICK_ORDER", "FFL_QB_PACE", "FFL_RECORD_FAKE", "FFL_ROLLOUT", "FFL_ROLLOUT_SIMS",
    "FFL_SESSION_COOKIE", "FFL_USER", "FFL_WRITES", "FFL_BACKUP_DEST", "FFL_DATA_DIR",
    "FFL_DB_URL", "FFL_TEST_PG",
    "FFL_RUNNER_URL", "FFL_RUNNER_PORT",  # set by docker-compose.yml, not .env
}


def test_env_example_covers_every_setting():
    # every "FFL_..." string literal in ffl/ is an environment name (os.environ, getenv, config helpers)
    read = {m for f in (ROOT / "ffl").rglob("*.py") for m in re.findall(r'"(FFL_[A-Z0-9_]+)"', f.read_text())}
    assert "FFL_SECRET_KEYS" in read and "FFL_JOB_TOKEN" in read
    text = (ROOT / "deploy/env.example").read_text()
    named = set(re.findall(r"^(FFL_[A-Z0-9_]+)=", text, re.M))
    assert read - DEV_ONLY - named == set()


def test_env_example_lines_are_commented():
    lines = (ROOT / "deploy/env.example").read_text().splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^[A-Z_]+=", line):
            assert i and lines[i - 1].startswith("#"), line
