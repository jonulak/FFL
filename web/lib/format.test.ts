import { describe, expect, it } from "vitest";
import { injuryTone, pts, signed } from "./format";

describe("format", () => {
  it("pts", () => { expect(pts(12.34)).toBe("12.3"); expect(pts(null)).toBe("—"); });
  it("signed", () => { expect(signed(3.2)).toBe("+3.2"); expect(signed(-1)).toBe("−1.0"); expect(signed(0)).toBe("+0.0"); });
  it("injuryTone", () => {
    expect(injuryTone("ACTIVE")).toBe("ok"); expect(injuryTone("QUESTIONABLE")).toBe("warn"); expect(injuryTone("OUT")).toBe("bad");
  });
});
