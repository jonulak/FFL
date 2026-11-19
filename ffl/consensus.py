"""One projection from several sources (the league's strategy, "Lineup" (`uv run python -m ffl.strategy show`) / "Waivers"): FantasyFootballAnalytics'
robust average, the Hodges-Lehmann estimator — the median of the Walsh averages (each value and every
pairwise mean). With one or two sources it is the mean, so the two-source number is the old one. Pure.
"""
from __future__ import annotations

import statistics
from itertools import combinations


def combine(values: dict[str, float]) -> tuple[float | None, float | None, int]:
    """(projection, spread, sources). spread: the range across sources once the single highest and
    lowest are dropped (n >= 4); with two sources |a - b|; None with one."""
    n = len(values)
    if n == 0:
        return None, None, 0
    vs = [float(v) for v in values.values()]
    walsh = vs + [(a + b) / 2 for a, b in combinations(vs, 2)]
    proj = round(statistics.median(walsh), 1)
    vals = sorted(float(v) for v in values.values())
    if n >= 4:
        vals = vals[1:-1]
    spread = round(vals[-1] - vals[0], 1) if n >= 2 else None
    return proj, spread, n

