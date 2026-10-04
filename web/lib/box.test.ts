import { describe, expect, it } from "vitest";
import { boxSum, count, points, rule } from "./box";

const item = (rate: number, flat: boolean, pts: number) => ({ stat_id: 1, label: "", count: 1, rate, flat, points: pts });

describe("box", () => {
  it("prints counts and points the way the breakdown does", () => {
    expect(count(250)).toBe("250");
    expect(count(1.5)).toBe("1.5");
    expect(points(5.52)).toBe("5.52");
    expect(points(8)).toBe("8.0");
    expect(points(-2)).toBe("−2.0");
  });
  it("states the league's rate", () => {
    expect(rule(item(-2, false, -2))).toBe("−2 each");
    expect(rule(item(1, true, 1))).toBe("+1 flat");
  });
  it("sums to the hundredth without float drift", () => {
    expect(boxSum([item(1, false, 0.1), item(1, false, 0.2)])).toBe(0.3);
    expect(boxSum([])).toBe(0);
  });
});
