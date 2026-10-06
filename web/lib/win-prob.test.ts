import { describe, expect, it } from "vitest";
import { favourite, fill, finalP, outcome, pct } from "./win-prob";

describe("the bar", () => {
  it("fills from the centre toward the favourite by the margin over even", () => {
    expect(fill(0.71)).toEqual({ left: 29, width: 21, end: "left" });
    expect(fill(0.29)).toEqual({ left: 50, width: 21, end: "right" });
  });
  it("is empty at even", () => {
    expect(fill(0.5)).toEqual({ left: 50, width: 0, end: "left" });
  });
  it("fills the whole half toward the winner of a final", () => {
    expect(fill(1)).toEqual({ left: 0, width: 50, end: "left" });
    expect(fill(0)).toEqual({ left: 50, width: 50, end: "right" });
  });
  it("reads a final off the scores as shown", () => {
    expect(finalP(120.4, 98.1)).toBe(1);
    expect(finalP(98.1, 120.4)).toBe(0);
    expect(finalP(10.1 + 20.2, 30.3)).toBe(0.5);
  });
});

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
