"""install.sh against a stub docker: a filled .env, compose up, idempotent; no docker is exit 2."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUB = """#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$*" in *"ffl.notify keys"*) echo VAPID_PUBLIC_KEY=pubkey; echo VAPID_PRIVATE_KEY=privkey;; esac
"""


def run(tmp_path, path):
    env = {"PATH": path, "HOME": str(tmp_path), "FFL_HOME": str(tmp_path / "ffl"),
           "FFL_INSTALL_BASE": f"file://{REPO}", "DOCKER_LOG": str(tmp_path / "docker.log")}
    return subprocess.run(["/bin/sh", str(REPO / "install.sh")], env=env, capture_output=True, text=True)


@pytest.fixture
def stub_path(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(STUB)
    (bin_ / "docker").chmod(0o755)
    return f"{bin_}:/usr/bin:/bin"


def test_install_fills_env_and_starts(tmp_path, stub_path):
    r = run(tmp_path, stub_path)
    assert r.returncode == 0, r.stderr
    env = (tmp_path / "ffl" / ".env").read_text()
    assert re.search(r"^FFL_SECRET_KEYS=k1:[A-Za-z0-9+/]{43}=$", env, re.M)
    for key in ("FFL_JOB_TOKEN", "POSTGRES_PASSWORD"):
        assert re.search(rf"^{key}=[0-9a-f]{{64}}$", env, re.M), key
    for line in ("VAPID_PUBLIC_KEY=pubkey", "VAPID_PRIVATE_KEY=privkey", "VAPID_SUBJECT=mailto:admin@localhost"):
        assert re.search(rf"^{line}$", env, re.M), line
    assert (tmp_path / "ffl" / ".env").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "ffl" / "docker-compose.yml").read_bytes() == (REPO / "docker-compose.yml").read_bytes()
    log = (tmp_path / "docker.log").read_text().splitlines()
    assert "compose pull" in log and log[-1] == "compose up -d"
    assert "Open http://localhost:3300" in r.stdout

    assert run(tmp_path, stub_path).returncode == 0
    assert (tmp_path / "ffl" / ".env").read_text() == env


def test_no_docker_exits_2(tmp_path):
    (tmp_path / "empty").mkdir()
    r = run(tmp_path, str(tmp_path / "empty"))
    assert r.returncode == 2 and "https://docs.docker.com/get-docker/" in r.stderr


def test_no_openssl_exits_2_and_writes_nothing(tmp_path, stub_path):
    r = run(tmp_path, stub_path.split(":")[0])  # the stub docker alone
    assert r.returncode == 2 and "openssl" in r.stderr
    assert not (tmp_path / "ffl").exists()


def test_keys_missing_fails(tmp_path, stub_path):
    (tmp_path / "bin" / "docker").write_text(STUB.replace("ffl.notify keys", "no-match"))
    r = run(tmp_path, stub_path)
    assert r.returncode == 1 and "VAPID" in r.stderr
    assert re.search(r"^VAPID_PUBLIC_KEY=$", (tmp_path / "ffl" / ".env").read_text(), re.M)


def test_runs_from_main():
    """A curl | sh stream cut short must run nothing, so the file's last line is the only call."""
    assert [ln for ln in (REPO / "install.sh").read_text().splitlines() if ln.strip()][-1] == 'main "$@"'


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not on PATH")
def test_shellcheck():
    subprocess.run(["shellcheck", "-s", "sh", str(REPO / "install.sh")], check=True)


def test_no_compose_plugin_exits_2(tmp_path, stub_path):
    (tmp_path / "bin" / "docker").write_text(STUB + 'case "$*" in "compose version") exit 1;; esac\n')
    r = run(tmp_path, stub_path)
    assert r.returncode == 2 and "compose plugin" in r.stderr
    assert not (tmp_path / "ffl").exists()
