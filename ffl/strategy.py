"""Strategy presets: the prose Claude reads while advising, and the few constants the draft tools take from it.

    uv run python -m ffl.strategy show [--league KEY]   # the league's strategy, as markdown
    uv run python -m ffl.strategy list                  # every preset and version

A preset lives in the `strategies` table under (key, version); a league plays the latest version of the key
`leagues.strategy` names. Presets are shared and not user-editable: only a migration adds one or a new version
(0016 added `2qb-10team` v1, STRATEGY.md's text; 0020 `1qb-10team` v1, the public league's). My caps are league data: a league takes its preset's
`suggested_caps` as its own the first time they are read, and from then on owns them (`ffl.leagues caps`), so a
new preset version never moves a cap.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import db, leagues


@dataclass(frozen=True)
class Strategy:
    key: str              # "2qb-10team"
    version: int
    name: str             # shown to users later; the prose never is
    prose: str            # markdown
    constants: dict       # {"qb_pace": 0.0, "suggested_caps": {"QB": 3, ...}}


def _strategy(row: dict) -> Strategy:
    return Strategy(row["key"], row["version"], row["name"], row["prose"], dict(row["constants"]))


def for_league(ref: leagues.LeagueRef) -> Strategy:
    """The league's linked preset, its latest version."""
    if not ref.strategy:
        raise SystemExit(f"{ref.key} plays no strategy preset (leagues.strategy is unset)")
    row = db.strategy_row(ref.strategy)
    if row is None:
        raise SystemExit(f"{ref.key} plays strategy {ref.strategy!r}, which has no stored version")
    return _strategy(row)


def caps(ref: leagues.LeagueRef) -> dict[str, int]:
    """`leagues.caps(ref)`, after any position with no cap of mine takes the preset's suggestion (never above
    the league's limit). A league with no preset keeps only the caps it has."""
    if ref.strategy:
        rows = db.caps_rows(ref.key)
        unset = [p for p in leagues.CAP_POSITIONS if rows.get(p, {}).get("my_cap") is None]
        if unset:
            suggested = for_league(ref).constants.get("suggested_caps") or {}
            seed = {}
            for p in unset:
                if p in suggested:
                    limit = rows.get(p, {}).get("provider_limit")
                    seed[p] = suggested[p] if limit is None else min(suggested[p], limit)
            if seed:
                db.seed_my_caps(ref.key, seed)
    return leagues.caps(ref)


def qb_pace(ref: leagues.LeagueRef) -> float:
    """Picks earlier than FFC ADP that this room takes its QBs: FFL_QB_PACE when set, else the preset's."""
    env = os.environ.get("FFL_QB_PACE")
    if env:
        try:
            return float(env)
        except ValueError:
            return 0.0
    if not ref.strategy:
        return 0.0
    return float(for_league(ref).constants.get("qb_pace") or 0.0)


def main(argv: list[str]) -> int:
    if argv[:1] == ["show"]:
        rest = argv[1:]
        ref = leagues.acting(rest[rest.index("--league") + 1] if "--league" in rest else None)
        print(for_league(ref).prose, end="")
        return 0
    if argv[:1] == ["list"]:
        playing = {}
        for r in leagues.all_leagues():
            if r.strategy:
                playing.setdefault(r.strategy, []).append(r.key)
        for row in db.strategy_rows():
            print(f"{row['key']}  v{row['version']}  {row['added_at']}  {row['name']}  "
                  f"{', '.join(playing.get(row['key'], [])) or '-'}")
        return 0
    print("usage: python -m ffl.strategy show [--league KEY] | list")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
