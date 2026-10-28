"""Secrets at rest (ffl-soc.3): ESPN cookies and push keys are sealed before they reach the database, so neither a row
nor a pg_dump carries them readable.

The key ring is FFL_SECRET_KEYS, outside the database, any dump and the repo: on the box /etc/ffl/env, rendered from
the SSM SecureString /ffl/env/FFL_SECRET_KEYS; in a worktree its .env (scripts/devdb.sh writes a fresh one). The ring
is `<id>:<base64 32 bytes>[,<id>:<base64>...]`: the first key seals, every key opens. A token is `<id>:<base64 nonce +
ciphertext>` (AES-256-GCM, a fresh 12-byte nonce each seal), its associated data the row's place (`<table>:<row>:<column>`),
so a token copied into another row or column does not open.

Rotation: put a new key first (`uv run python -m ffl.vault keygen`), run `uv run python -m ffl.vault rotate` (every
token sealed with an older key is sealed again with the first), then drop the old key from the ring.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV = "FFL_SECRET_KEYS"
_NONCE = 12


class VaultError(Exception):
    """No usable ring, or a token this ring cannot open (another ring's, moved, or tampered with)."""


def ring() -> list[tuple[str, bytes]]:
    """The ring as (id, key) pairs, the sealing key first; read at each call, so a test or a rotation can change it."""
    raw = (os.environ.get(ENV) or "").strip()
    if not raw:
        raise VaultError(f"{ENV} is not set: `uv run python -m ffl.vault keygen` makes a key (see ffl/vault.py)")
    out: list[tuple[str, bytes]] = []
    for part in raw.split(","):
        kid, sep, b64 = part.strip().partition(":")
        if not sep or not kid or any(c in kid for c in " :"):
            raise VaultError(f"{ENV}: each entry is <id>:<base64 key>")
        try:
            key = base64.b64decode(b64, validate=True)
        except (binascii.Error, ValueError):
            raise VaultError(f"{ENV}: key {kid!r} is not base64") from None
        if len(key) != 32:
            raise VaultError(f"{ENV}: key {kid!r} is {len(key)} bytes, not 32")
        if any(kid == k for k, _ in out):
            raise VaultError(f"{ENV}: key id {kid!r} appears twice")
        out.append((kid, key))
    return out


def keygen() -> str:
    """A new ring entry: a random id and key."""
    return f"{secrets.token_hex(4)}:{base64.b64encode(os.urandom(32)).decode()}"


def seal(plain: str, *, aad: str) -> str:
    kid, key = ring()[0]
    nonce = os.urandom(_NONCE)
    ct = AESGCM(key).encrypt(nonce, plain.encode(), aad.encode())
    return f"{kid}:{base64.b64encode(nonce + ct).decode()}"


def key_id(token: str) -> str:
    return token.partition(":")[0]


def stale(token: str) -> bool:
    """Sealed with a key other than the ring's first: what `rotate` seals again."""
    return key_id(token) != ring()[0][0]


def open_(token: str, *, aad: str) -> str:
    kid, sep, b64 = (token or "").partition(":")
    key = dict(ring()).get(kid)
    if not sep or key is None:
        raise VaultError(f"sealed with key {kid!r}, which the ring does not hold")
    try:
        blob = base64.b64decode(b64, validate=True)
        return AESGCM(key).decrypt(blob[:_NONCE], blob[_NONCE:], aad.encode()).decode()
    except (binascii.Error, ValueError, InvalidTag):
        raise VaultError("the token does not open here (another row's, or altered)") from None


def seal_json(value, *, aad: str) -> str:
    return seal(json.dumps(value), aad=aad)


def open_json(token: str, *, aad: str):
    return json.loads(open_(token, aad=aad))


def main(argv: list[str]) -> int:
    if argv == ["keygen"]:
        print(keygen())
        return 0
    if argv == ["rotate"]:
        from . import db
        print(f"sealed again with the ring's first key: {db.reseal_all()}")
        return 0
    print("usage: python -m ffl.vault keygen | rotate", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
