import { describe, expect, it } from "vitest";
import type { OutlookBullet, PlayerOutlook } from "./api";
import { outlookPhase } from "./waiver-outlook";

const take: OutlookBullet = { label: "Take", text: "A stash.", sources: [{ site: "Rotowire", date: "10/1", url: "https://x.test" }] };
const o = (kw: Partial<PlayerOutlook>): PlayerOutlook => ({ bullets: null, at: null, ...kw });

describe("outlookPhase", () => {
  it("is loading until the first read lands", () => {
    expect(outlookPhase(null)).toBe("loading");
  });
  it("is empty when nothing is stored", () => {
    expect(outlookPhase(o({}))).toBe("empty");
  });
  it("shows the bullets once written", () => {
    expect(outlookPhase(o({ bullets: [take], at: "2026-10-02T09:00-04:00" }))).toBe("ready");
  });
});
