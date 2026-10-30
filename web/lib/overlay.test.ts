import { describe, expect, it } from "vitest";
import type { League, Roster, TeamRoster, Week } from "./api";
import fixtures from "./overlay.fixtures.json";
import { type LiveDelta, type LiveState, merge, overlayLeague, overlayRoster, overlayTeam, overlayWeek } from "./overlay";

// tests/test_live_overlay_fixtures.py: a payload the server overlaid at feed states 0, 1, 2 and the stream's
// deltas 0→1 and 1→2. Any served payload with the deltas since laid on must be the server's answer at that state.
const deltas = fixtures.deltas as unknown as LiveDelta[];
const apply = (kind: string, d: unknown, live: LiveState): unknown => {
  if (kind === "week") return overlayWeek(d as Week, live);
  if (kind === "roster") return overlayRoster(d as Roster, live);
  if (kind === "team") return overlayTeam(d as TeamRoster, live);
  return overlayLeague(d as League, live);
};
// win_prob through JS's erf stand-in may sit 0.001 off the Python's rounding; every other number is exact
const probs = (x: unknown): unknown => JSON.parse(JSON.stringify(x), (k, v) => (k === "win_prob" && v
  ? Object.fromEntries(Object.entries(v).map(([s, n]) => [s, typeof n === "number" ? Math.round(n * 100) : n]))
  : v));

describe("overlay parity with ffl/live.py", () => {
  for (const c of fixtures.cases) {
    for (let j = 1; j < 3; j++) {
      for (let i = 0; i <= j; i++) {
        it(`${c.kind}: served at ${i}, deltas to ${j}`, () => {
          let live: LiveState | null = null;
          for (const d of deltas.slice(0, j)) live = merge(live, d).live;
          const got = apply(c.kind, c.served[i], live!);
          if (i < j) expect(c.served[i]).not.toEqual(c.served[j]); // the case moves: a no-op overlay cannot pass
          expect(probs(got)).toEqual(probs(c.served[j]));
        });
      }
    }
  }
});

describe("merge", () => {
  const d = deltas[0];
  it("opens the week on the first delta without a refetch", () => {
    expect(merge(null, d).refetch).toBe(false);
  });
  it("refetches on a week alone or another week", () => {
    const { live } = merge(null, d);
    expect(merge(live, { v: 9, week: d.week + 1 }).refetch).toBe(true);
    expect(merge(live, { ...d, week: d.week + 1 }).refetch).toBe(true);
  });
  it("leaves a payload of another week alone", () => {
    const { live } = merge(null, d);
    const other = { ...(fixtures.cases[0].served[0] as unknown as Week), week: d.week - 1 };
    expect(overlayWeek(other, live)).toBe(other);
  });
});
