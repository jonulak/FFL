"""install.sh against a stub docker: a filled .env, compose up, idempotent; no docker is exit 2."""
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
STUB = """#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
"""


def run(tmp_path, path, **extra):
    env = {"PATH": path, "HOME": str(tmp_path), "FFL_HOME": str(tmp_path / "ffl"),
           "FFL_INSTALL_BASE": f"file://{REPO}", "DOCKER_LOG": str(tmp_path / "docker.log"), **extra}
    return subprocess.run(["/bin/sh", str(REPO / "install.sh")], env=env, capture_output=True, text=True)


@pytest.fixture
def stub_path(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text(STUB)
    (bin_ / "docker").chmod(0o755)
    return f"{bin_}:/usr/bin:/bin"


def test_install_fills_env_and_starts(tmp_path, stub_path):
    (tmp_path / "bin" / "nc").write_text("#!/bin/sh\nexit 1\n")  # every port reads free, whatever the host serves
    (tmp_path / "bin" / "nc").chmod(0o755)
    r = run(tmp_path, stub_path)
    assert r.returncode == 0, r.stderr
    env = (tmp_path / "ffl" / ".env").read_text()
    assert re.search(r"^FFL_SECRET_KEYS=k1:[A-Za-z0-9+/]{43}=$", env, re.M)
    for key in ("FFL_JOB_TOKEN", "POSTGRES_PASSWORD"):
        assert re.search(rf"^{key}=[0-9a-f]{{64}}$", env, re.M), key
    # everything else is set in the app and kept in the database: .env holds the bootstrap secrets and the port only
    assert sorted(re.findall(r"^([A-Z_]+)=", env, re.M)) == [
        "FFL_JOB_TOKEN", "FFL_PORT", "FFL_SECRET_KEYS", "FFL_VERSION", "POSTGRES_PASSWORD", "TZ"]
    assert (tmp_path / "ffl" / ".env").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "ffl" / "docker-compose.yml").read_bytes() == (REPO / "docker-compose.yml").read_bytes()
    log = (tmp_path / "docker.log").read_text().splitlines()
    assert "compose pull" in log and log[-2:] == ["compose up -d --wait --wait-timeout 600", "image prune -f"]
    assert _val(tmp_path, "FFL_PORT") == "3300"
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


def test_runs_from_main():
    """A curl | sh stream cut short must run nothing, so the file's last line is the only call."""
    assert [ln for ln in (REPO / "install.sh").read_text().splitlines() if ln.strip()][-1] == 'main "$@"'


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not on PATH")
def test_shellcheck():
    subprocess.run(["shellcheck", "-s", "sh", str(REPO / "install.sh")], check=True)


def test_db_not_published():
    """Only the web port is opened on the host; Postgres stays on the compose network."""
    compose = (REPO / "docker-compose.yml").read_text()
    db = compose.split("\n  db:\n", 1)[1].split("\n  api:\n", 1)[0]
    assert "ports:" not in db


def test_no_compose_plugin_exits_2(tmp_path, stub_path):
    (tmp_path / "bin" / "docker").write_text(STUB + 'case "$*" in "compose version") exit 1;; esac\n')
    r = run(tmp_path, stub_path)
    assert r.returncode == 2 and "compose plugin" in r.stderr
    assert not (tmp_path / "ffl").exists()


def _busy_env(tmp_path):
    """A listening socket and a kept .env naming its port as the web port."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    web = sock.getsockname()[1]
    env = (REPO / "deploy" / "env.example").read_text()
    env = re.sub(r"^FFL_PORT=.*$", f"FFL_PORT={web}", env, flags=re.M)
    (tmp_path / "ffl").mkdir()
    (tmp_path / "ffl" / ".env").write_text(env)
    return sock, web


def _val(tmp_path, key):
    return re.search(rf"^{key}=(.*)$", (tmp_path / "ffl" / ".env").read_text(), re.M).group(1)


@pytest.mark.skipif(not shutil.which("nc"), reason="nc not on PATH")
def test_busy_web_port_moves(tmp_path, stub_path):
    sock, web = _busy_env(tmp_path)
    r = run(tmp_path, stub_path)
    sock.close()
    assert r.returncode == 0, r.stderr
    new_web = int(_val(tmp_path, "FFL_PORT"))
    assert new_web > web
    assert f"Open http://localhost:{new_web}" in r.stdout and "in use" in r.stderr


@pytest.mark.skipif(not shutil.which("nc"), reason="nc not on PATH")
def test_running_stack_keeps_port(tmp_path, stub_path):
    """A re-run beside the stack it started sees its own port busy: it must not move."""
    (tmp_path / "bin" / "docker").write_text(STUB + 'case "$*" in "compose ps"*) echo abc123;; esac\n')
    sock, web = _busy_env(tmp_path)
    r = run(tmp_path, stub_path)
    sock.close()
    assert r.returncode == 0, r.stderr
    assert _val(tmp_path, "FFL_PORT") == str(web)


def test_rerun_is_the_upgrade_it_replaces_a_stale_compose_file(tmp_path, stub_path):
    assert run(tmp_path, stub_path).returncode == 0
    compose = tmp_path / "ffl" / "docker-compose.yml"
    compose.write_text("services:\n  db:\n    image: postgres:15\n")  # an older release's file
    env = (tmp_path / "ffl" / ".env").read_text()
    assert run(tmp_path, stub_path).returncode == 0
    assert compose.read_bytes() == (REPO / "docker-compose.yml").read_bytes()
    assert (tmp_path / "ffl" / ".env").read_text() == env


def test_a_failed_prune_does_not_fail_the_upgrade(tmp_path, stub_path):
    (tmp_path / "bin" / "docker").write_text(STUB + 'case "$*" in "image prune"*) exit 1;; esac\n')
    r = run(tmp_path, stub_path)
    assert r.returncode == 0, r.stderr
    assert "Open http://localhost:" in r.stdout and "old images" in r.stderr


def test_a_failed_start_keeps_the_old_images(tmp_path, stub_path):
    (tmp_path / "bin" / "docker").write_text(STUB + 'case "$*" in "compose up"*) exit 1;; esac\n')
    assert run(tmp_path, stub_path).returncode != 0
    assert "image prune -f" not in (tmp_path / "docker.log").read_text().splitlines()


CURL = """#!/bin/sh
for a; do case $a in https://*) url=$a;; esac; [ "$prev" = -o ] && out=$a; prev=$a; done
echo "$url" >> "$CURL_LOG"
cp "$REPO/${url#*/FFL/*/}" "$out"
"""


@pytest.mark.parametrize("version, ref", [("latest", "release"), ("v1.2.0", "v1.2.0"),
                                          ("sha-abc1234", "abc1234")])  # the rollback's pin: an image tag, not a git ref
def test_compose_file_comes_from_the_pinned_release(tmp_path, stub_path, version, ref):
    """With no FFL_INSTALL_BASE the files come over curl (a stub serving the repo's): from the tag FFL_VERSION pins,
    else `release`, the tag images.yml moves once all four images are out (main's file can be ahead of them)."""
    (tmp_path / "bin" / "curl").write_text(CURL)
    (tmp_path / "bin" / "curl").chmod(0o755)
    (tmp_path / "ffl").mkdir()
    env = re.sub(r"^FFL_VERSION=.*$", f"FFL_VERSION={version}", (REPO / "deploy" / "env.example").read_text(),
                 flags=re.M)
    (tmp_path / "ffl" / ".env").write_text(env)
    r = subprocess.run(["/bin/sh", str(REPO / "install.sh")], capture_output=True, text=True, env={
        "PATH": stub_path, "HOME": str(tmp_path), "FFL_HOME": str(tmp_path / "ffl"), "REPO": str(REPO),
        "DOCKER_LOG": str(tmp_path / "docker.log"), "CURL_LOG": str(tmp_path / "curl.log")})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "curl.log").read_text().split() == [
        f"https://raw.githubusercontent.com/jonulak/FFL/{ref}/docker-compose.yml"]


def test_api_has_a_healthcheck_for_up_wait():
    """`up --wait` is only as good as this: without it a restart-looping API would count as started."""
    compose = (REPO / "docker-compose.yml").read_text()
    api = compose.split("\n  api:\n", 1)[1].split("\n  runner:\n", 1)[0]
    assert "healthcheck:" in api and "8300/health" in api


# ---- --auto: the scheduled upgrade -------------------------------------------------------------

OLD, NEW = "a" * 40, "b" * 40
UP = "compose up -d --wait --wait-timeout 600"
AUTO_DOCKER = STUB + """case "$*" in
  "compose ps"*) [ -n "${STACK_RUNNING-1}" ] && echo abc123;;
  "compose exec -T api"*) [ -n "${API_DOWN:-}" ] && exit 1; echo "{\\"hold\\":${HOLD:-false}}";;
  "compose pull") [ -n "${PULL_FAILS:-}" ] && exit 1;;
  "compose exec -T jobs"*) [ -n "${BACKUP_FAILS:-}" ] && exit 1;;
  "compose up"*) [ -n "${UP_FAILS:-}" ] && exit 1;;
esac
exit 0
"""
AUTO_CURL = """#!/bin/sh
for a; do case $a in https://*) url=$a;; esac; [ "$prev" = -o ] && out=$a; prev=$a; done
echo "$url" >> "$CURL_LOG"
case $url in
  https://api.github.com/*) [ -n "${GITHUB_DOWN:-}" ] && exit 22
    printf '{\\n  "ref": "refs/tags/release",\\n  "object": {\\n    "sha": "%s",\\n    "type": "commit"\\n  }\\n}\\n' "$RELEASE";;
  *) cp "$REPO/${url#*/FFL/*/}" "$out";;
esac
"""


@pytest.fixture
def auto(tmp_path, stub_path):
    """A kept install on OLD, the release at NEW; returns a runner of `install.sh --auto`."""
    for name, body in (("docker", AUTO_DOCKER), ("curl", AUTO_CURL)):
        (tmp_path / "bin" / name).write_text(body)
        (tmp_path / "bin" / name).chmod(0o755)
    home = tmp_path / "ffl"
    home.mkdir()
    (home / ".env").write_text((REPO / "deploy" / "env.example").read_text())
    (home / ".upgrade-last").write_text(OLD + "\n")

    def go(*args, **extra):
        env = {"PATH": stub_path, "HOME": str(tmp_path), "FFL_HOME": str(home), "REPO": str(REPO), "RELEASE": NEW,
               "DOCKER_LOG": str(tmp_path / "docker.log"), "CURL_LOG": str(tmp_path / "curl.log"), **extra}
        return subprocess.run(["/bin/sh", str(REPO / "install.sh"), *(args or ("--auto",))], env=env,
                              capture_output=True, text=True)
    return go


def _log(tmp_path):
    path = tmp_path / "docker.log"
    return path.read_text().splitlines() if path.exists() else []


def _upgraded(tmp_path):
    return "compose pull" in _log(tmp_path)


def _last(tmp_path):
    return (tmp_path / "ffl" / ".upgrade-last").read_text().strip()


def test_auto_upgrades_from_the_exact_release_commit_after_a_backup(tmp_path, auto):
    r = auto()
    assert r.returncode == 0, r.stderr
    # the commit, not the tag: raw.githubusercontent caches a tag's files for minutes, and the images have already moved
    assert f"https://raw.githubusercontent.com/jonulak/FFL/{NEW}/docker-compose.yml" in (tmp_path / "curl.log").read_text()
    log = _log(tmp_path)
    backup = next(i for i, ln in enumerate(log) if "ffl.db backup /backups/pre-upgrade" in ln)
    # through the jobs container: the same image as the api, up even when a bad release has the api restarting
    assert log[backup].startswith("compose exec -T jobs ")
    assert log.index("compose pull") < backup < log.index(UP)
    assert log[-1] == "image prune -f"
    assert _last(tmp_path) == NEW


def test_auto_does_nothing_on_the_commit_it_last_tried(tmp_path, auto):
    (tmp_path / "ffl" / ".upgrade-last").write_text(NEW + "\n")
    assert auto().returncode == 0
    assert not _upgraded(tmp_path)


def test_auto_waits_for_live_games_without_marking_the_release(tmp_path, auto):
    assert auto(HOLD="true").returncode == 0
    assert not _upgraded(tmp_path) and _last(tmp_path) == OLD
    assert auto(HOLD="false").returncode == 0  # the games ended: the next tick takes it
    assert _upgraded(tmp_path) and _last(tmp_path) == NEW


def test_auto_goes_ahead_when_the_api_cannot_say(tmp_path, auto):
    """A broken API is what a new release may fix."""
    assert auto(API_DOWN="1").returncode == 0
    assert _upgraded(tmp_path) and _last(tmp_path) == NEW


def test_auto_leaves_a_stopped_stack_stopped(tmp_path, auto):
    assert auto(STACK_RUNNING="").returncode == 0
    assert not _upgraded(tmp_path) and UP not in _log(tmp_path)


def test_auto_leaves_a_pinned_install_alone(tmp_path, auto):
    env = tmp_path / "ffl" / ".env"
    env.write_text(re.sub(r"^FFL_VERSION=.*$", "FFL_VERSION=v1.2.0", env.read_text(), flags=re.M))
    assert auto().returncode == 0
    assert not _upgraded(tmp_path) and _last(tmp_path) == OLD


def test_auto_says_so_when_the_schedule_cannot_find_docker(tmp_path, auto):
    """A PATH that lost docker would otherwise read as a stack stopped on purpose: an empty log, forever."""
    (tmp_path / "empty").mkdir()  # no /usr/bin: a CI runner has a real docker there
    r = auto(PATH=str(tmp_path / "empty"))
    assert r.returncode == 0 and "docker is not on this job's PATH" in r.stderr


def test_auto_does_nothing_when_github_cannot_say(tmp_path, auto):
    assert auto(GITHUB_DOWN="1").returncode == 0
    assert not _upgraded(tmp_path) and _last(tmp_path) == OLD


@pytest.mark.parametrize("fail", ["PULL_FAILS", "BACKUP_FAILS"])
def test_auto_retries_a_failure_before_the_stack_was_touched(tmp_path, auto, fail):
    assert auto(**{fail: "1"}).returncode != 0
    assert _last(tmp_path) == OLD and UP not in _log(tmp_path)
    assert not (tmp_path / "ffl" / ".upgrade-lock").exists()
    assert auto().returncode == 0
    assert _last(tmp_path) == NEW


def test_auto_does_not_retry_a_release_that_failed_to_start(tmp_path, auto):
    r = auto(UP_FAILS="1")
    assert r.returncode != 0 and "logs api" in r.stderr
    assert _last(tmp_path) == NEW and "image prune -f" not in _log(tmp_path)
    (tmp_path / "docker.log").unlink()
    assert auto().returncode == 0  # the next tick: no second backup, no second restart
    assert not _upgraded(tmp_path)
    assert auto(RELEASE="c" * 40).returncode == 0  # a newer release is tried
    assert _upgraded(tmp_path) and _last(tmp_path) == "c" * 40


def test_auto_skips_while_another_run_holds_the_lock(tmp_path, auto):
    (tmp_path / "ffl" / ".upgrade-lock").mkdir()
    assert auto().returncode == 0
    assert not _upgraded(tmp_path) and (tmp_path / "ffl" / ".upgrade-lock").exists()


def test_auto_clears_a_lock_left_by_a_killed_run(tmp_path, auto):
    lock = tmp_path / "ffl" / ".upgrade-lock"
    lock.mkdir()
    os.utime(lock, (0, 0))
    assert auto().returncode == 0
    assert _upgraded(tmp_path) and not lock.exists()


# ---- --auto-on / --auto-off: the schedule ------------------------------------------------------

def _uname(tmp_path, name):
    (tmp_path / "bin" / "uname").write_text(f"#!/bin/sh\necho {name}\n")
    (tmp_path / "bin" / "uname").chmod(0o755)


def test_auto_on_and_off_with_cron(tmp_path, auto):
    _uname(tmp_path, "Linux")
    tab = tmp_path / "crontab.txt"
    tab.write_text("0 1 * * * mine\n")
    # stdin read whole before the file is touched, as the real one does: its own `crontab -l` is the other end of the pipe
    (tmp_path / "bin" / "crontab").write_text(
        f'#!/bin/sh\ncase $1 in -l) cat "{tab}";; -) new=$(cat); printf "%s\\n" "$new" > "{tab}";; esac\n')
    (tmp_path / "bin" / "crontab").chmod(0o755)
    assert auto("--auto-on").returncode == 0
    assert auto("--auto-on").returncode == 0  # twice is one entry
    lines = tab.read_text().splitlines()
    assert lines[0] == "0 1 * * * mine" and len(lines) == 2
    assert lines[1].startswith("*/5 * * * * ") and "/FFL/release/install.sh" in lines[1] and "--auto" in lines[1]
    assert f"FFL_HOME='{tmp_path / 'ffl'}'" in lines[1] and "PATH='" in lines[1]  # cron knows neither on its own
    assert auto("--auto-off").returncode == 0
    assert tab.read_text().splitlines() == ["0 1 * * * mine"]


def test_auto_on_and_off_with_launchd(tmp_path, auto):
    _uname(tmp_path, "Darwin")
    calls = tmp_path / "launchctl.log"
    (tmp_path / "bin" / "launchctl").write_text(f'#!/bin/sh\necho "$1" >> "{calls}"\n')
    (tmp_path / "bin" / "launchctl").chmod(0o755)
    assert auto("--auto-on").returncode == 0
    path = tmp_path / "Library" / "LaunchAgents" / "com.jonulak.ffl.auto-upgrade.plist"
    plist = plistlib.loads(path.read_bytes())
    assert plist["StartInterval"] == 300
    assert "/FFL/release/install.sh" in plist["ProgramArguments"][-1] and "--auto" in plist["ProgramArguments"][-1]
    assert plist["EnvironmentVariables"]["FFL_HOME"] == str(tmp_path / "ffl")
    assert calls.read_text().split() == ["bootout", "bootstrap"]
    assert auto("--auto-off").returncode == 0
    assert not path.exists()


def test_auto_on_refuses_without_an_install(tmp_path, auto):
    (tmp_path / "ffl" / ".env").unlink()
    r = auto("--auto-on")
    assert r.returncode == 2 and "install" in r.stderr.lower()
