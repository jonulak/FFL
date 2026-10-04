import { describe, expect, it } from "vitest";
import type { SeasonLog, WeekLog } from "./api";
import { columns, ptsText, seasonPts, weekLabel } from "./player-log";

const wk = (week: number, summary: [string, string][] = [], status: WeekLog["status"] = "played", pts: number | null = null): WeekLog =>
  ({ week, opp: null, home: null, status, pts, summary, box: [] });
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
    expect(ptsText(12.25)).toBe("12.3");
  });
  it("sums the season from its weeks, the live one included", () => {
    expect(seasonPts([wk(1, [], "played", 10.2), wk(2, [], "bye"), wk(3, [], "live", 4.1)])).toEqual({ games: 2, pts: 14.3 });
  });
});
