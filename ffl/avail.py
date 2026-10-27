"""Who is still on the board at a position, straight from the socket feed.

    uv run python -m ffl.avail WR        # top 8 undrafted WRs by projection
    uv run python -m ffl.avail RB 12
    uv run python -m ffl.avail D/ST

Sorted by projection, not score, so it shows the players the candidate table hides once a
position sits at replacement level. Use it before naming anyone late in the draft. For the current
league (FFL_LEAGUE, else FFL_USER's default): the board is the pre-draft one, rebuilt from the DB's
draft inputs; the picks gone come from its pick feed (the DB's draft_feeds).
"""
from __future__ import annotations

import sys

from . import board as board_mod
from . import db, leagues


def main(pos: str, n: int = 8):
    ref = leagues.acting()
    gone = {p["playerId"] for p in db.draft_feed(ref)[0]}
    b = board_mod.build(*board_mod.load_inputs(ref))
    left = b[(b.pos == pos.upper()) & ~b.player_id.isin(gone)].sort_values("proj", ascending=False)
    print(f"{len(gone)} picks made — {len(left)} {pos.upper()} left:")
    cols = ["player_id", "name", "team", "bye", "proj", "vorp", "adp", "injury", "return_week", "snap", "tgt", "trend"]
    print(left.head(n)[[c for c in cols if c in left.columns]].to_string(index=False))  # the last four: mid-season


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8)
