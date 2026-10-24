"""The league registry: every league the app knows, the home one included, lives in the `leagues` table.

    uv run python -m ffl.leagues list
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import db


@dataclass(frozen=True)
class LeagueRef:
    key: str                     # "espn:100000001"
    provider: str                # "espn"
    provider_id: str             # "100000001"
    season: int
    name: str
    team_id: int | None
    home: bool
    lineup_format: str | None    # set by task 3
    strategy: str | None         # set by task 4


_FIELDS = ("key", "provider", "provider_id", "season", "name", "team_id", "home", "lineup_format", "strategy")


def key_for(provider: str, provider_id: str | int) -> str:
    return f"{provider}:{provider_id}"


def _ref(row: dict) -> LeagueRef:
    return LeagueRef(**{k: row[k] for k in _FIELDS})


def all_leagues() -> list[LeagueRef]:
    """Every registered league, home first."""
    return [_ref(r) for r in db.league_rows()]


def home() -> LeagueRef:
    for ref in all_leagues():
        if ref.home:
            return ref
    raise SystemExit("no home league: run `uv run python -m ffl.leagues add espn <id> --home`")


def get(key: str) -> LeagueRef:
    for ref in all_leagues():
        if ref.key == key:
            return ref
    raise SystemExit(f"unknown league {key!r}; known: {', '.join(r.key for r in all_leagues()) or 'none'}")


def current() -> LeagueRef:
    """FFL_LEAGUE (read now, not at import) names a league; unset is the home league. TRANSITIONAL (1b removes the default)."""
    key = os.environ.get("FFL_LEAGUE")
    return get(key) if key else home()


def register(ref: LeagueRef) -> LeagueRef:
    db.put_league({k: getattr(ref, k) for k in _FIELDS})
    return get(ref.key)


def main(argv: list[str]) -> int:
    if argv[:1] == ["list"]:
        for r in all_leagues():
            print(f"{r.key}  {r.season}  team {r.team_id if r.team_id is not None else '-'}  "
                  f"{'home' if r.home else '    '}  {r.name}")
        return 0
    print("usage: python -m ffl.leagues list")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
