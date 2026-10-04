import { describe, expect, it } from "vitest";
import { aheadLabel, expLabel, formCell, formItems, injuryLabel, oppTrend, injuryTone, ordinal, pts, record, signed } from "./format";

describe("standings", () => {
  it("sets a record with ties only when there are any", () => {
    expect(record({ wins: 3, losses: 1, ties: 0 })).toBe("3-1");
    expect(record({ wins: 2, losses: 1, ties: 1 })).toBe("2-1-1");
  });
  it("puts a place in words", () => {
    expect([1, 2, 3, 4, 10, 11, 12, 13, 21, 22, 103].map(ordinal)).toEqual(
      ["1st", "2nd", "3rd", "4th", "10th", "11th", "12th", "13th", "21st", "22nd", "103rd"]);
  });
});

describe("format", () => {
  it("pts", () => { expect(pts(12.34)).toBe("12.3"); expect(pts(null)).toBe("—"); });
  it("signed", () => { expect(signed(3.2)).toBe("+3.2"); expect(signed(-1)).toBe("−1.0"); expect(signed(0)).toBe("+0.0"); });
  it("injuryTone", () => {
    expect(injuryTone("ACTIVE")).toBe("ok"); expect(injuryTone("QUESTIONABLE")).toBe("warn"); expect(injuryTone("OUT")).toBe("bad");
  });
  it("injuryLabel", () => {
    expect(injuryLabel("OUT", 8, 5)).toBe("OUT · wk 8");
    expect(injuryLabel("OUT", 5, 5)).toBe("OUT");
    expect(injuryLabel("OUT", 3, 5)).toBe("OUT");
    expect(injuryLabel("QUESTIONABLE", null, 5)).toBe("QUESTIONABLE");
  });
});

describe("formItems", () => {
  const base = { games: 2, weeks: [1, 2], l3: 3.7, beat: -7.2, opp: 6, opp_trend: 2, snap_pct: 25, snap_games: 1, tgt_snap: 0.07, ypt: 1.6, pace_gap: -74.0, weight: 1.4, games_left: 15 };
  it("lists every column a running back has, in reading order, with the game count on L", () => {
    const items = formItems(base);
    expect(items.map((i) => `${i.label} ${i.value}`)).toEqual([
      "L2 3.7", "beat −7.2", "opp 6.0", "snap 25%", "tgt/snap 0.07", "ypt 1.6", "pace −74.0", "wt 1.4",
    ]);
    expect(items[2].trend).toBe("up");
    expect(items[3].title).toContain("1 of the 2 games");
  });
  it("leaves out the columns his position has no number for", () => {
    const k = formItems({ ...base, snap_pct: null, tgt_snap: null, ypt: null });
    expect(k.map((i) => i.label)).toEqual(["L2", "beat", "opp", "pace", "wt"]);
    const dst = formItems({ ...base, opp: null, opp_trend: null, snap_pct: null, tgt_snap: null, ypt: null });
    expect(dst.map((i) => i.label)).toEqual(["L2", "beat", "pace", "wt"]);
  });
  it("has no trend for a single game or a flat one", () => {
    expect(oppTrend({ ...base, opp_trend: null })).toBeNull();
    expect(oppTrend({ ...base, opp_trend: 0 })).toBeNull();
    expect(oppTrend({ ...base, opp_trend: -3 })).toBe("down");
  });
});

describe("formCell", () => {
  const base = { games: 2, weeks: [1, 2], l3: 3.7, beat: -7.2, opp: 6, opp_trend: 2, snap_pct: 25, snap_games: 1, tgt_snap: 0.07, ypt: 1.6, pace_gap: -74.0, weight: 1.4, games_left: 15 };
  it("notes a short window and partial snaps under the number, and keeps the column label", () => {
    expect(formCell(base, "l3")).toMatchObject({ label: "L3", value: "3.7", note: "2 games" });
    expect(formCell({ ...base, games: 3, weeks: [1, 2, 3] }, "l3")?.note).toBeUndefined();
    expect(formCell(base, "snap_pct")).toMatchObject({ value: "25%", note: "1 of 2" });
    expect(formCell({ ...base, snap_games: 2 }, "snap_pct")?.note).toBeUndefined();
    expect(formCell({ ...base, tgt_snap: null }, "tgt_snap")).toBeNull();
    expect(formCell({ ...base, ypt: null }, "ypt")).toBeNull();
  });
  it("shows the window's weight to the blend, with the game count when it fell short", () => {
    expect(formCell({ ...base, weight: 1.4 }, "weight")).toMatchObject({ label: "wt", value: "1.4", note: "of 2" });
    expect(formCell({ ...base, weight: 2 }, "weight")).toMatchObject({ value: "2.0" });
    expect(formCell({ ...base, weight: 2 }, "weight")?.note).toBeUndefined();
  });
});

describe("exp and ahead", () => {
  it("labels a rookie R and a veteran by his years", () => {
    expect(expLabel(0)).toBe("R");
    expect(expLabel(6)).toBe("6y");
    expect(expLabel(null)).toBeNull();
  });
  it("lists the hurt teammates ahead of him with the return week while it is still ahead", () => {
    const ahead = [{ name: "RJ Harvey", status: "OUT", return_week: 3 }, { name: "J.K. Dobbins", status: "QUESTIONABLE", return_week: null }];
    expect(aheadLabel(ahead, 2)).toBe("Harvey OUT · wk 3, Dobbins Q");
    expect(aheadLabel(ahead, 3)).toBe("Harvey OUT, Dobbins Q");
    expect(aheadLabel([], 3)).toBeNull();
  });
});
