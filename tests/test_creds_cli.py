"""ffl.creds: a user's ESPN cookies in from stdin, never argv, and never printed back."""
import io

import pytest

from ffl import creds, espn_auth
from tests.conftest import TEST_USER

S2, SWID = "s2-cli-" + "z" * 30, "{CLI-SWID}"


def _stdin(monkeypatch, text: str):
    monkeypatch.setattr("sys.stdin", io.StringIO(text))


def test_set_reads_the_env_lines_from_stdin_and_prints_no_value(monkeypatch, capsys):
    _stdin(monkeypatch, f"TZ=America/New_York\nESPN_S2={S2}\nSWID={SWID}\n")
    assert creds.main(["set", "--user", TEST_USER["email"]]) == 0
    out = capsys.readouterr()
    assert S2 not in out.out + out.err and SWID not in out.out + out.err
    assert espn_auth.own(0).jar() == {"espn_s2": S2, "SWID": SWID}


def test_set_takes_quoted_lowercase_names_too(monkeypatch):
    _stdin(monkeypatch, f"espn_s2=\"{S2}\"\nswid='{SWID}'\n")
    assert creds.main(["set", "--user", TEST_USER["email"]]) == 0
    assert espn_auth.own(0).swid == SWID


def test_set_with_one_missing_refuses_and_stores_nothing(monkeypatch):
    _stdin(monkeypatch, f"ESPN_S2={S2}\n")
    with pytest.raises(SystemExit, match="SWID"):
        creds.main(["set", "--user", TEST_USER["email"]])
    assert espn_auth.status(0)["connected"] is False


def test_status_names_the_user_and_no_value(monkeypatch, capsys):
    espn_auth.connect(0, S2, SWID)
    assert creds.main(["status"]) == 0
    out = capsys.readouterr().out
    assert TEST_USER["email"] in out and "connected" in out and S2 not in out and SWID not in out


def test_clear(monkeypatch):
    espn_auth.connect(0, S2, SWID)
    assert creds.main(["clear", "--user", TEST_USER["email"]]) == 0
    assert espn_auth.own(0) is None


def test_rotate_seals_everything_with_the_new_first_key(monkeypatch):
    from ffl import db, vault
    import os
    espn_auth.connect(0, S2, SWID)
    db.put_subscription("https://push/x", {"p256dh": "k", "auth": "a"}, user_id=0)
    old = os.environ["FFL_SECRET_KEYS"]
    new = vault.keygen()
    monkeypatch.setenv("FFL_SECRET_KEYS", f"{new},{old}")
    assert db.reseal_all() == 2
    monkeypatch.setenv("FFL_SECRET_KEYS", new)   # the old key dropped: everything still opens
    assert espn_auth.own(0).swid == SWID
    assert db.subscriptions(user_id=0)[0]["keys"] == {"p256dh": "k", "auth": "a"}
    assert db.reseal_all() == 0
