"""Text checks on the Docker images and the jobs' crontab (no docker needed)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_api_dockerfile():
    text = (ROOT / "Dockerfile").read_text()
    assert "FROM python:3.12-slim" in text
    assert "nodejs" not in text and "npm " not in text and "@anthropic-ai/claude-code" not in text  # the runner's (#99)
    assert "postgresql-client-18" in text
    assert "deploy/crontab /etc/crontabs/root" in text


def test_runner_dockerfile_and_service():
    """The runner: the standalone claude binary (no Node), harness.py alone, and no published port (#99)."""
    import yaml
    text = (ROOT / "Dockerfile.runner").read_text()
    assert "claude.ai/install.sh" in text and "npm" not in text and "COPY ffl/__init__.py ffl/harness.py" in text
    svc = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert "ports" not in svc["runner"] and "env_file" not in svc["runner"]
    assert svc["api"]["environment"]["FFL_RUNNER_URL"] == "http://runner:8310"


def test_crontab_has_the_six_jobs():
    text = (ROOT / "deploy/crontab").read_text()
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    expected = {
        "*/15 * * * *": "/notify/tick",
        "*/10 * * * *": "/news/run",
        "5 * * * *": "/outlook/refresh",
        "35 * * * *": "/transactions/sync",
        "0 7 * * 3": "ffl.calibrate wednesday",
        "30 3 * * *": "ffl.db backup /backups",
    }
    assert len(lines) == len(expected)
    for sched, job in expected.items():
        assert any(
            l.startswith(sched + " ") and job in l and l.endswith(">> /proc/1/fd/1 2>&1")
            for l in lines
        ), sched


def test_next_standalone():
    text = (ROOT / "web/next.config.ts").read_text()
    assert 'output: "standalone"' in text
    assert "tailnet-name" not in text


def test_jobs_crond_command():
    """Debian's busybox crond has no -d (it prints usage and exits 1): -L sends its messages to the log."""
    import yaml
    cmd = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["jobs"]["command"]
    assert cmd == ["crond", "-f", "-L", "/dev/stdout"] and "-d" not in cmd
