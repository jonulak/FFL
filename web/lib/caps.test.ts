import { describe, expect, it } from "vitest";
import { parseCap } from "./caps";

describe("parseCap", () => {
  it("reads a whole count of players", () => {
    expect(parseCap("3", 4)).toEqual({ ok: 3 });
    expect(parseCap(" 0 ", null)).toEqual({ ok: 0 });
  });
  it("never above the league's limit", () => {
    expect(parseCap("5", 4)).toEqual({ error: "The league allows at most 4." });
    expect(parseCap("9", null)).toEqual({ ok: 9 });
  });
  it("refuses what is not a count", () => {
    expect(parseCap("", 4)).toEqual({ error: "A cap is a count of players." });
    expect(parseCap("2.5", 4)).toEqual({ error: "A cap is a count of players." });
    expect(parseCap("-1", 4)).toEqual({ error: "A cap is a count of players." });
  });
});
