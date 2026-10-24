"""Who is still on the board at a position, straight from the socket feed.

    uv run python -m ffl.avail WR        # top 8 undrafted WRs by projection
    uv run python -m ffl.avail RB 12
    uv run python -m ffl.avail D/ST

Sorted by projection, not score, so it shows the players the candidate table hides once a
position sits at replacement level. Use it before naming anyone late in the draft. The board is
the pre-draft one, rebuilt from the DB's draft inputs; the picks gone come from the socket feed.
"""
from __future__ import annotations

import json
import sys

from . import board as board_mod
from .config import DATA


def main(pos: str, n: int = 8):
    picks = DATA / "socket_picks.json"
    gone = {p["playerId"] for p in json.loads(picks.read_text())} if picks.exists() else set()
    b = board_mod.build(*board_mod.load_inputs())
    left = b[(b.pos == pos.upper()) & ~b.player_id.isin(gone)].sort_values("proj", ascending=False)
    print(f"{len(gone)} picks made — {len(left)} {pos.upper()} left:")
    print(left.head(n)[["player_id", "name", "team", "bye", "proj", "vorp", "adp", "injury"]].to_string(index=False))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8)
