"""The install's own settings (Settings › App, the setup wizard): sealed rows in `app_secrets`, read through a short cache.

Nothing here comes from the environment: `.env` holds only what the app needs before it reaches its database.
"""
from __future__ import annotations

import time

from . import db, vault

CLAUDE_TOKEN = "claude_token"
VAPID_PUBLIC = "vapid_public"
VAPID_PRIVATE = "vapid_private"
PUSH_CONTACT = "push_contact"
APP_URL = "app_url"
YOUTUBE_KEY = "youtube_api_key"

TTL_S = 60
_CACHE: dict[str, tuple[str | None, float]] = {}


def forget() -> None:
    """Drop the cache (a save or a removal, on any key)."""
    _CACHE.clear()


def get(key: str) -> str | None:
    """The opened value, None with no row or one this key ring cannot open (said once a minute at most)."""
    hit = _CACHE.get(key)  # read once: forget() on a request thread may clear the dict mid-read
    if hit is None or time.monotonic() - hit[1] > TTL_S:
        try:
            value = db.get_secret(key)
        except vault.VaultError as e:  # sealed under a ring this host lacks: as if none were stored
            print(f"appconf: the stored {key} does not open: {e}")
            value = None
        hit = _CACHE[key] = (value, time.monotonic())
    return hit[0]


def put(key: str, value: str | None) -> None:
    """Store `value` sealed; None or blank deletes the row."""
    if value:
        db.put_secret(key, value)
    else:
        db.del_secret(key)
    forget()


def app_url() -> str:
    """The address people open the app at (stored at /setup, edited in Settings › App)."""
    return (get(APP_URL) or "http://localhost:3300").rstrip("/")
