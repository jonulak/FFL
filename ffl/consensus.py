"""One projection from several sources (STRATEGY.md "Lineup" / "Waivers"): FantasyFootballAnalytics'
robust average, the Hodges-Lehmann estimator — the median of the Walsh averages (each value and every
pairwise mean). With one or two sources it is the mean, so the two-source number is the old one.

Weights (fitted by `ffl.calibrate sources`, applied once activated) weight a pairwise mean by the
product of its two sources' weights and a single value by its weight squared, then take the weighted
median; equal weights give the unweighted answer. Pure.
"""
from __future__ import annotations

import time
from itertools import combinations


def _weighted_median(items: list[tuple[float, float]]) -> float:
    items = sorted(items)
    total = sum(w for _, w in items)
    cum = 0.0
    for i, (v, w) in enumerate(items):
        cum += w
        if abs(cum - total / 2) < 1e-9 and i + 1 < len(items):
            return (v + items[i + 1][0]) / 2   # exactly half below: the unweighted median's midpoint
        if cum > total / 2:
            return v
    return items[-1][0]


def combine(values: dict[str, float], weights: dict[str, float] | None = None
            ) -> tuple[float | None, float | None, int]:
    """(projection, spread, sources). spread: the range across sources once the single highest and
    lowest are dropped (n >= 4); with two sources |a - b|; None with one."""
    n = len(values)
    if n == 0:
        return None, None, 0
    srcs = sorted(values)
    if weights:
        known = [weights[s] for s in srcs if s in weights]
        fill = sum(known) / len(known) if known else 1.0
        w = {s: float(weights.get(s, fill)) for s in srcs}
    else:
        w = {s: 1.0 for s in srcs}
    items = [(float(values[s]), w[s] * w[s]) for s in srcs]
    items += [((values[a] + values[b]) / 2, w[a] * w[b]) for a, b in combinations(srcs, 2)]
    proj = round(_weighted_median(items), 1)
    vals = sorted(float(v) for v in values.values())
    if n >= 4:
        vals = vals[1:-1]
    spread = round(vals[-1] - vals[0], 1) if n >= 2 else None
    return proj, spread, n


_W: list = [0.0, None]   # [read_at, the active fit] — read again after WEIGHTS_TTL_S
WEIGHTS_TTL_S = 300


def weights_for(kind: str, pos: str) -> dict[str, float] | None:
    """The activated fit's weights for "week" or "ros" at a position; None (equal weights) when no fit
    is active or the DB cannot be read."""
    from . import db
    if time.time() - _W[0] > WEIGHTS_TTL_S:
        try:
            fit = db.active_source_weights()
        except db.DBError:
            fit = None
        _W[:] = [time.time(), fit]
    return ((_W[1] or {}).get(kind) or {}).get(pos)


def forget() -> None:
    _W[:] = [0.0, None]
