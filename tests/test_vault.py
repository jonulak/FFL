"""ffl-soc.3: secrets at rest are sealed with a key ring kept outside the database (FFL_SECRET_KEYS)."""
import pytest

from ffl import vault


def test_a_sealed_value_opens_with_its_ring_and_its_place():
    token = vault.seal("s2-secret", aad="espn_credentials:7:secret")
    assert "s2-secret" not in token
    assert vault.open_(token, aad="espn_credentials:7:secret") == "s2-secret"


def test_two_seals_of_one_value_differ():
    """A fresh nonce each time: equal cookies never show as equal ciphertexts."""
    assert vault.seal("x", aad="a") != vault.seal("x", aad="a")


def test_a_token_moved_to_another_row_or_column_does_not_open():
    token = vault.seal("s2-secret", aad="espn_credentials:7:secret")
    for elsewhere in ("espn_credentials:8:secret", "push_subscriptions:7:keys"):
        with pytest.raises(vault.VaultError):
            vault.open_(token, aad=elsewhere)


def test_another_ring_cannot_open_it(monkeypatch):
    token = vault.seal("s2-secret", aad="a")
    monkeypatch.setenv("FFL_SECRET_KEYS", vault.keygen())
    with pytest.raises(vault.VaultError):
        vault.open_(token, aad="a")


def test_a_tampered_token_does_not_open():
    token = vault.seal("s2-secret", aad="a")
    kid, body = token.split(":", 1)
    flipped = body[:-2] + ("A" if body[-2] != "A" else "B") + body[-1]
    with pytest.raises(vault.VaultError):
        vault.open_(f"{kid}:{flipped}", aad="a")


def test_no_ring_is_an_error_naming_the_fix(monkeypatch):
    monkeypatch.delenv("FFL_SECRET_KEYS", raising=False)
    with pytest.raises(vault.VaultError, match="FFL_SECRET_KEYS"):
        vault.seal("x", aad="a")


def test_a_malformed_ring_is_refused(monkeypatch):
    key = vault.keygen().split(":")[1]
    for bad in ("nocolon", "k1:not-base64!", "k1:QUJD", f"k1:{key},k1:{key}", ""):
        monkeypatch.setenv("FFL_SECRET_KEYS", bad)
        with pytest.raises(vault.VaultError):
            vault.ring()


def test_rotation_seals_with_the_first_key_and_still_opens_the_old(monkeypatch):
    old_ring = vault.keygen()
    monkeypatch.setenv("FFL_SECRET_KEYS", old_ring)
    old = vault.seal("x", aad="a")
    new_key = vault.keygen()
    monkeypatch.setenv("FFL_SECRET_KEYS", f"{new_key},{old_ring}")
    assert vault.key_id(old) == old_ring.split(":")[0]
    assert vault.stale(old)
    assert vault.open_(old, aad="a") == "x"
    fresh = vault.seal("x", aad="a")
    assert vault.key_id(fresh) == new_key.split(":")[0] and not vault.stale(fresh)


def test_json_round_trip():
    keys = {"p256dh": "BPk...", "auth": "zz"}
    assert vault.open_json(vault.seal_json(keys, aad="a"), aad="a") == keys
