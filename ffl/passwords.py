"""Password hashing (scrypt, stdlib) and the username rule."""
import base64
import hashlib
import hmac
import re
import secrets

MIN_PASSWORD = 8
_USERNAME = re.compile(r"[A-Za-z0-9._-]{2,32}")


def _scrypt(pw: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${_b64(salt)}${_b64(_scrypt(pw, salt))}"


def verify(pw: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        salt_b = base64.b64decode(salt, validate=True)
        want = base64.b64decode(digest, validate=True)
        return hmac.compare_digest(_scrypt(pw, salt_b), want)
    except ValueError:  # malformed stored value (binascii.Error is a ValueError)
        return False


def check_username(raw: str) -> str | None:
    name = raw.strip()
    return name if _USERNAME.fullmatch(name) else None
