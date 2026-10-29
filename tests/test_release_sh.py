"""deploy/box/release.sh: its argument check and the choice of releases to prune (the parts that run off the box).

The script is sourced (its main() runs only when executed), so the pure functions are called on their own.
"""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "box" / "release.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"


def bash(snippet: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; {snippet}', "release-test", *args],
        capture_output=True, text=True,
    )


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def test_valid_sha():
    assert bash('valid_sha "$1"', SHA).returncode == 0
    for bad in ["", SHA[:-1], SHA + "0", SHA.upper(), SHA[:-1] + "g", SHA + "\n", f"{SHA}; reboot",
                "../" + SHA[3:], " " + SHA[1:]]:
        assert bash('valid_sha "$1"', bad).returncode != 0, repr(bad)


def test_main_refuses_bad_arguments_before_anything_else():
    for args in [(), (SHA, "extra"), ("not-a-sha",), (SHA.upper(),)]:
        r = run(*args)
        assert r.returncode == 2, (args, r.stderr)
        assert "deployed" not in r.stdout


def test_main_needs_root():
    if os.geteuid() == 0:
        return
    r = run(SHA)
    assert r.returncode == 2 and "run as root" in r.stderr


def _releases(tmp_path: Path, names_oldest_first: list[str]) -> Path:
    rel = tmp_path / "releases"
    rel.mkdir()
    for i, name in enumerate(names_oldest_first):
        d = rel / name
        d.mkdir()
        os.utime(d, (1_000_000 + i * 100, 1_000_000 + i * 100))
    return rel


def prune(rel: Path, keep: int, current: Path) -> list[str]:
    r = bash('prune_list "$1" "$2" "$3"', str(rel), str(keep), str(current))
    assert r.returncode == 0, r.stderr
    return [Path(line).name for line in r.stdout.splitlines()]


def test_prune_keeps_the_newest_three(tmp_path):
    rel = _releases(tmp_path, ["b2", "a" * 40, "b" * 40, "c" * 40, "d" * 40])
    assert prune(rel, 3, rel / ("d" * 40)) == ["a" * 40, "b2"]


def test_prune_never_removes_current(tmp_path):
    # current pointed back at an old release: it stays, beside the newest three
    rel = _releases(tmp_path, ["b2", "a" * 40, "b" * 40, "c" * 40, "d" * 40])
    assert prune(rel, 3, rel / "b2") == ["a" * 40]
    assert prune(rel, 3, Path(str(rel / "b2") + "/")) == ["a" * 40]


def test_prune_with_few_or_no_releases(tmp_path):
    rel = _releases(tmp_path, ["b2", "a" * 40])
    assert prune(rel, 3, rel / ("a" * 40)) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert prune(empty, 3, empty / SHA) == []


def test_prune_skips_symlinks_and_files(tmp_path):
    rel = _releases(tmp_path, ["a" * 40, "b" * 40, "c" * 40, "d" * 40])
    (rel / "link").symlink_to(tmp_path)
    (rel / "file").write_text("x")
    (rel / ".hidden").mkdir()
    assert prune(rel, 3, rel / ("d" * 40)) == ["a" * 40]


# --- the env guard (ffl-soc.6.2.1): runs before `current` is switched, on /etc/ffl/env (a path argument here)
GOOD = {
    "FFL_APP_URL": "https://ffl.example",
    "FFL_GOOGLE_CLIENT_ID": "cid-SECRETVALUE-1",
    "FFL_GOOGLE_CLIENT_SECRET": "csecret-SECRETVALUE-2",
    "FFL_SIGNUP_EMAILS": "me@example.com",
    "FFL_SECRET_KEYS": "k1:SECRETVALUE-3",
    "FFL_JOB_TOKEN": "",
    "FFL_AUTH_SECRET": "",
}


def _env(tmp_path: Path, **over) -> Path:
    vals = {**GOOD, **over}
    p = tmp_path / "env"
    p.write_text("ESPN_S2=keepme\n" + "".join(f"{k}={v}\n" for k, v in vals.items() if v is not None) + "TZ=America/New_York\n")
    p.chmod(0o640)
    return p


def guard(p: Path) -> subprocess.CompletedProcess:
    return bash('release_env "$1"', str(p))


def _value(p: Path, name: str) -> str:
    for line in p.read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1]
    return ""


def test_release_refuses_dev_signin(tmp_path):
    r = guard(_env(tmp_path, FFL_DEV_SIGNIN="1"))
    assert r.returncode != 0
    assert "FFL_DEV_SIGNIN" in r.stderr


def test_release_refuses_missing_google_client(tmp_path):
    for name in ("FFL_GOOGLE_CLIENT_ID", "FFL_GOOGLE_CLIENT_SECRET"):
        for val in ("", None):
            r = guard(_env(tmp_path, **{name: val}))
            assert r.returncode != 0, (name, val)
            assert name in r.stderr
            assert "SECRETVALUE" not in r.stderr + r.stdout


def test_release_refuses_empty_signup_list(tmp_path):
    r = guard(_env(tmp_path, FFL_SIGNUP_EMAILS=""))
    assert r.returncode != 0 and "FFL_SIGNUP_EMAILS" in r.stderr


def test_release_refuses_no_secret_ring(tmp_path):
    """ffl-soc.3: never generated here, as the job token is: a new ring would not open the rows sealed with the old."""
    for val in ("", None):
        r = guard(_env(tmp_path, FFL_SECRET_KEYS=val))
        assert r.returncode != 0 and "FFL_SECRET_KEYS" in r.stderr, val


def test_release_refuses_empty_app_url(tmp_path):
    r = guard(_env(tmp_path, FFL_APP_URL=""))
    assert r.returncode != 0 and "FFL_APP_URL" in r.stderr


def test_release_fills_job_token_and_auth_secret(tmp_path):
    p = _env(tmp_path, FFL_AUTH_SECRET=None)
    before = p.stat()
    r = guard(p)
    assert r.returncode == 0, r.stderr
    for name in ("FFL_JOB_TOKEN", "FFL_AUTH_SECRET"):
        v = _value(p, name)
        assert len(v) == 64 and all(c in "0123456789abcdef" for c in v), name
        assert v not in r.stdout + r.stderr
    text = p.read_text()
    assert "ESPN_S2=keepme\n" in text and "TZ=America/New_York\n" in text
    assert p.stat().st_mode == before.st_mode and p.stat().st_uid == before.st_uid
    assert [x.name for x in tmp_path.iterdir()] == ["env"]
    tok = _value(p, "FFL_JOB_TOKEN")
    assert guard(p).returncode == 0 and _value(p, "FFL_JOB_TOKEN") == tok


def test_release_fills_nothing_when_it_refuses(tmp_path):
    p = _env(tmp_path, FFL_SIGNUP_EMAILS="")
    assert guard(p).returncode != 0
    assert _value(p, "FFL_JOB_TOKEN") == ""


def test_release_refuses_whitespace_only_values(tmp_path):
    for val in (" ", "\t ", '" "', "''"):
        r = guard(_env(tmp_path, FFL_SIGNUP_EMAILS=val))
        assert r.returncode != 0 and "FFL_SIGNUP_EMAILS" in r.stderr, repr(val)


def test_release_accepts_a_quoted_value_and_pads(tmp_path):
    assert guard(_env(tmp_path, FFL_SIGNUP_EMAILS='"me@example.com"')).returncode == 0
    assert guard(_env(tmp_path, FFL_SIGNUP_EMAILS=" me@example.com ")).returncode == 0


def test_a_failed_fill_leaves_the_env_file_untouched(tmp_path):
    p = _env(tmp_path)
    original = p.read_bytes()
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool in ("sed", "cat"):
        (stubs / tool).write_text("#!/bin/sh\nexit 1\n")
        (stubs / tool).chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; release_env "$1"', "t", str(p)],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert p.read_bytes() == original
    assert [x.name for x in tmp_path.iterdir() if x.name.startswith("env.")] == []


def test_a_failed_mktemp_leaves_the_env_file_untouched(tmp_path):
    p = _env(tmp_path)
    original = p.read_bytes()
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "mktemp").write_text("#!/bin/sh\nexit 1\n")
    (stubs / "mktemp").chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; release_env "$1"', "t", str(p)],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0 and p.read_bytes() == original


def test_units_carry_the_job_token():
    d = SCRIPT.parent / "systemd"
    for n in ("notify", "news", "transactions", "outlook"):
        t = (d / f"ffl-{n}.service").read_text()
        assert '-H "x-ffl-job: ${FFL_JOB_TOKEN}"' in t, n
        assert "EnvironmentFile=/etc/ffl/env" in t


# --- box_drift: the root-run pieces a release ships but must never install itself ---

def _box(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """A release's deploy/box (release.sh, Caddyfile, two units) and the box's installed copies, all the same."""
    rel = tmp_path / "rel"
    (rel / "deploy" / "box" / "systemd").mkdir(parents=True)
    (rel / "deploy" / "box" / "release.sh").write_text("#!/bin/sh\necho v1\n")
    (rel / "deploy" / "box" / "Caddyfile").write_text("ffl.example { }\n")
    for u in ("ffl-api.service", "ffl-news.timer"):
        (rel / "deploy" / "box" / "systemd" / u).write_text(f"[Unit]\n# {u}\n")
    installed = tmp_path / "opt-release.sh"
    installed.write_text("#!/bin/sh\necho v1\n")
    units = tmp_path / "units"
    units.mkdir()
    for u in ("ffl-api.service", "ffl-news.timer"):
        (units / u).write_text(f"[Unit]\n# {u}\n")
    caddy = tmp_path / "Caddyfile"
    caddy.write_text("ffl.example { }\n")
    return rel, installed, units, caddy


def drift(rel: Path, installed: Path, units: Path, caddy: Path) -> list[str]:
    r = bash('box_drift "$1" "$2" "$3" "$4"', str(rel), str(installed), str(units), str(caddy))
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def test_box_drift_is_empty_when_the_box_has_the_releases_copies(tmp_path):
    assert drift(*_box(tmp_path)) == []


def test_box_drift_names_each_changed_or_new_piece(tmp_path):
    rel, installed, units, caddy = _box(tmp_path)
    (rel / "deploy" / "box" / "release.sh").write_text("#!/bin/sh\necho v2\n")
    (rel / "deploy" / "box" / "systemd" / "ffl-news.timer").write_text("[Timer]\nOnCalendar=*:0/5\n")
    (rel / "deploy" / "box" / "systemd" / "ffl-new.service").write_text("[Unit]\n")   # not on the box at all
    (rel / "deploy" / "box" / "Caddyfile").write_text("ffl.example { encode }\n")
    assert sorted(drift(rel, installed, units, caddy)) == [
        "deploy/box/Caddyfile", "deploy/box/release.sh", "deploy/box/systemd/ffl-new.service",
        "deploy/box/systemd/ffl-news.timer"]


def test_box_drift_ignores_what_setup_sh_does_not_install(tmp_path):
    rel, installed, units, caddy = _box(tmp_path)
    (rel / "deploy" / "box" / "systemd" / "README").write_text("notes\n")
    (units / "beads-update.timer").write_text("[Timer]\n")    # another repo's unit on the box
    assert drift(rel, installed, units, caddy) == []


def test_main_refuses_a_release_that_changes_box_pieces_before_switching():
    """The check sits between the unpack and the switch: a drifting release is never made current."""
    src = SCRIPT.read_text()
    check = src.index('box_drift "$rel"')
    assert src.index('say "unpack to $rel"') < check < src.index('say "switch $ROOT/current')
    assert check < src.index('say "uv sync --frozen"'), "refuse before the slow steps"
