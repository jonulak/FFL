import { describe, expect, it } from "vitest";
import { favourite, outcome, pct } from "./win-prob";

describe("win probability", () => {
  it("rounds to whole percent and never says certain before the whistle", () => {
    expect(pct(0.713)).toBe("71%");
    expect(pct(0.5)).toBe("50%");
    expect(pct(0.996)).toBe(">99%");
    expect(pct(0.004)).toBe("<1%");
    expect(pct(0.994)).toBe("99%");
  });
  it("reads a final as W, L or T", () => {
    expect(outcome(1)).toBe("W");
    expect(outcome(0)).toBe("L");
    expect(outcome(0.5)).toBe("T");
  });
  it("names the favourite", () => {
    expect(favourite({ abbrev: "JON", p: 0.29 }, { abbrev: "DAN", p: 0.71 })).toBe("DAN 71%");
    expect(favourite({ abbrev: "JON", p: 0.502 }, { abbrev: "DAN", p: 0.498 })).toBe("50–50");
  });
});
