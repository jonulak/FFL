"""uv run python -m ffl.sources compare | status

compare: every rostered player's weekly and rest-of-season projection the old way (ESPN + Sleeper
mean; Sleeper + CBS mean) beside the consensus, biggest moves first: the eyeball check before landing.
status: each source's health (what GET /sources/status serves).
"""
from __future__ import annotations

import json
import sys

from .. import db, espn_season as E, leagues
from . import status_snapshot


def _old(vals: dict[str, float], keep: tuple[str, ...]) -> float | None:
    have = [v for s, v in vals.items() if s in keep]
    return round(sum(have) / len(have), 1) if have else None


def compare(*, ref) -> None:
    lg = E.fresh_league(ref)
    settings = E.load(ref)
    week = lg.current_week
    rw = E.ros_week_for(lg, week)
    players = [E.flatten_box_player(bp) for lineup in E._boxes_by_team(lg, week, ref=ref).values() for bp in lineup]
    # the season lookup first: it sets the ESPN pool keys the weekly unmatched-name log is read against
    lookup = E.season_lookup(settings, rw, ref)
    src = E.weekly_sources(week, settings)
    print("sources this week:", {name: len(d) for name, d in src.items()})
    rows = []
    for p, m in zip(players, E.merge_weekly(players, src)):
        vals = E.source_values(p, src)
        ros = (lookup.get(p["player_id"]) or {})
        rows.append((p["name"], p["pos"], _old(vals, ("espn", "sleeper")), m["proj"], m["n_src"],
                     _old(ros.get("ros_src") or {}, ("sleeper", "cbs")), ros.get("ros_proj"), ros.get("ros_n")))
    rows.sort(key=lambda r: -abs((r[3] or 0) - (r[2] or 0)))
    print(f"{'player':<26}{'pos':<5}{'wk old':>8}{'wk new':>8}{'n':>3}{'ros old':>9}{'ros new':>9}{'n':>3}")
    for r in rows:
        print(f"{r[0]:<26}{r[1]:<5}{r[2] if r[2] is not None else '—':>8}{r[3]:>8}{r[4]:>3}"
              f"{r[5] if r[5] is not None else '—':>9}{r[6] if r[6] is not None else '—':>9}{r[7] or 0:>3}")


def main(argv: list[str]) -> None:
    if argv[:1] == ["compare"]:
        compare(ref=leagues.acting())
    elif argv[:1] == ["status"]:
        print(json.dumps({**(db.get_state("source_status", {}) or {}), **status_snapshot()}, indent=2))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
