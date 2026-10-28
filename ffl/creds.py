"""A user's ESPN cookies, stored sealed in their own row (ffl-soc.3; ffl/espn_auth.py). Values come in on stdin, never
argv, and are never printed:

    uv run python -m ffl.creds set --user <email>     # stdin: ESPN_S2=... and SWID=... lines (a tty asks for each)
    uv run python -m ffl.creds status [--user <email>]
    uv run python -m ffl.creds clear --user <email>

`--user` defaults to FFL_USER. Other KEY=VALUE lines on stdin are ignored, so the box's one-off can pipe the two lines
of an env file straight in: `grep -E '^(ESPN_S2|SWID)=' /etc/ffl/env | ... ffl.creds set --user <email>`.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import db, espn_auth, leagues

NAMES = {"espn_s2": "espn_s2", "swid": "swid"}


def _read() -> tuple[str, str]:
    if sys.stdin.isatty():
        return getpass.getpass("espn_s2: "), getpass.getpass("SWID: ")
    got: dict[str, str] = {}
    for line in sys.stdin.read().splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and (key := NAMES.get(name.strip().lower())):
            got[key] = value.strip().strip("\"'")
    missing = [n for n, k in (("ESPN_S2", "espn_s2"), ("SWID", "swid")) if not got.get(k)]
    if missing:
        raise SystemExit(f"no {' or '.join(missing)} line on stdin: nothing stored")
    return got["espn_s2"], got["swid"]


def _user(email: str | None) -> dict:
    return leagues._user(email) if email else leagues.acting_user()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m ffl.creds")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("set", "status", "clear"):
        sub.add_parser(name).add_argument("--user", help="the user's sign-in email (default: FFL_USER)")
    args = ap.parse_args(argv)
    if args.cmd == "set":
        user = _user(args.user)
        espn_s2, swid = _read()
        espn_auth.connect(user["id"], espn_s2, swid)
        print(f"ESPN cookies stored for {user['email']} (sealed)")
        return 0
    if args.cmd == "clear":
        user = _user(args.user)
        espn_auth.disconnect(user["id"])
        print(f"ESPN cookies cleared for {user['email']}")
        return 0
    rows = db.credential_statuses()
    if args.user:
        uid = _user(args.user)["id"]
        rows = [r for r in rows if r["user_id"] == uid]
    if not rows:
        print("no ESPN connections stored")
    for r in rows:
        st = espn_auth.status(r["user_id"])
        state = ("unreadable (another key ring's)" if st["unreadable"] else
                 f"failing since {r['failing_since']}: {r['failing_reason']}" if r["failing_since"] else "connected")
        print(f"{r['email']}: {state}; stored {r['updated_at']}, last ok {r['last_ok'] or 'never'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
