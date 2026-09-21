import { describe, expect, it } from "vitest";
import { formItems, injuryLabel, injuryTone, pts, signed } from "./format";

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
  const base = { games: 2, weeks: [1, 2], l3: 3.7, beat: -7.2, opp: 6, opp_trend: 2, snap_pct: 25, snap_games: 1, tgt_snap: 0.07, ypt: 1.6, pace_gap: -74.0 };
  it("lists every column a running back has, in reading order, with the game count on L", () => {
    const items = formItems(base);
    expect(items.map((i) => `${i.label} ${i.value}`)).toEqual([
      "L2 3.7", "beat −7.2", "opp 6.0 ↑", "snap 25%", "tgt/snap 0.07", "ypt 1.6", "pace −74.0",
    ]);
    expect(items[3].title).toContain("1 of the 2 games");
  });
  it("leaves out the columns his position has no number for", () => {
    const k = formItems({ ...base, snap_pct: null, tgt_snap: null, ypt: null });
    expect(k.map((i) => i.label)).toEqual(["L2", "beat", "opp", "pace"]);
    const dst = formItems({ ...base, opp: null, opp_trend: null, snap_pct: null, tgt_snap: null, ypt: null });
    expect(dst.map((i) => i.label)).toEqual(["L2", "beat", "pace"]);
  });
  it("shows no trend arrow for a single game or a flat one", () => {
    expect(formItems({ ...base, opp_trend: null })[2].value).toBe("6.0");
    expect(formItems({ ...base, opp_trend: 0 })[2].value).toBe("6.0");
    expect(formItems({ ...base, opp_trend: -3 })[2].value).toBe("6.0 ↓");
  });
});
