import { describe, expect, it } from "vitest";
import { holeLines, tradePath } from "./trade";

describe("tradePath", () => {
  it("names the team alone until a player is picked", () => {
    expect(tradePath(4, [], [])).toBe("/trade?team=4");
  });

  it("repeats give and get, a D/ST's negative id included", () => {
    expect(tradePath(4, [1, -16001], [3])).toBe("/trade?team=4&give=1&give=-16001&get=3");
  });
});

describe("holeLines", () => {
  it("folds a slot's weeks into ranges, whichever slot empties first leading", () => {
    const holes = [11, 16, 17, 18].map((week) => ({ week, slot: week === 11 ? "K" : "QB" }));
    expect(holeLines(holes)).toEqual(["K empty wk 11", "QB empty wks 16–18"]);
  });

  it("says a broken run and a flex by its label, and a second empty slot in one week once", () => {
    const holes = [{ week: 9, slot: "RB/WR/TE" }, { week: 11, slot: "RB/WR/TE" }, { week: 11, slot: "RB/WR/TE" }, { week: 12, slot: "RB/WR/TE" }];
    expect(holeLines(holes)).toEqual(["FLEX empty wks 9, 11–12"]);
  });
});
