import { describe, expect, it } from "vitest";
import type { SeasonLog, WeekLog } from "./api";
import { cells, columns, hasSnaps, headerInjury, ptsText, seasonPts, weekLabel } from "./player-log";

const wk = (week: number, summary: [string, string][] = [], status: WeekLog["status"] = "played", pts: number | null = null): WeekLog =>
  ({ week, opp: null, home: null, status, pts, summary, box: [], volume: null });
const totals = { games: 0, pts: 0, ppg: 0, summary: [] as [string, string][], box: [] };

describe("player-log", () => {
  it("takes the column labels from the first week with a summary", () => {
    const s: SeasonLog = { season: 2025, weeks: [wk(1, [], "bye"), wk(2, [["C/A", "1/2"], ["YDS", "9"]])], totals };
    expect(columns(s)).toEqual(["C/A", "YDS"]);
    expect(columns({ season: 2025, weeks: [], totals: { ...totals, summary: [["REC", "3"]] } })).toEqual(["REC"]);
  });
  it("labels weeks and points", () => {
    expect(weekLabel(wk(7))).toBe("W7");
    expect(ptsText(null)).toBe("—");
    expect(ptsText(12.254)).toBe("12.25");
  });
  it("adds the return week to the header chip only when the week is known and it is still ahead", () => {
    expect(headerInjury("OUT", 4)).toBe("OUT");
    expect(headerInjury("OUT", 4, 4)).toBe("OUT");
    expect(headerInjury("OUT", 6, 4)).toBe("OUT · wk 6");
  });
  it("adds a SNAP column after the stat group when the season carries snaps", () => {
    const s: SeasonLog = { season: 2026, weeks: [{ ...wk(1, [["CAR", "20"]]), snap_pct: 74 }, { ...wk(2, [["CAR", "9"]]), snap_pct: null }], totals };
    expect(columns(s)).toEqual(["CAR", "SNAP"]);
    expect(cells(s.weeks[0], true)).toEqual([["CAR", "20"], ["SNAP", "74%"]]);
    expect(cells(s.weeks[1], true)).toEqual([["CAR", "9"], ["SNAP", "—"]]);
    // a past season (no snaps in it) keeps its stat group alone
    const past: SeasonLog = { season: 2025, weeks: [wk(1, [["CAR", "20"]])], totals };
    expect(columns(past)).toEqual(["CAR"]);
    expect(cells(past.weeks[0], hasSnaps(past))).toEqual([["CAR", "20"]]);
  });
  it("sums the season from its weeks, the live one included", () => {
    expect(seasonPts([wk(1, [], "played", 10.2), wk(2, [], "bye"), wk(3, [], "live", 4.1)])).toEqual({ games: 2, pts: 14.3 });
  });
});
