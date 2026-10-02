import { describe, expect, it } from "vitest";
import type { OutlookBullet, WaiverOutlook } from "./api";
import { outlookPhase } from "./waiver-outlook";

const take: OutlookBullet = { label: "Take", text: "A stash.", sources: [{ site: "Rotowire", date: "10/1", url: "https://x.test" }] };
const o = (kw: Partial<WaiverOutlook>): WaiverOutlook => ({ bullets: null, at: null, stale: false, running: false, error: null, ...kw });

describe("outlookPhase", () => {
  it("is loading until the first read lands", () => {
    expect(outlookPhase(null)).toBe("loading");
  });
  it("offers the write when nothing is stored", () => {
    expect(outlookPhase(o({}))).toBe("empty");
  });
  it("says writing while a write runs, even over old bullets or an old failure", () => {
    expect(outlookPhase(o({ running: true }))).toBe("writing");
    expect(outlookPhase(o({ running: true, bullets: [take], error: "RuntimeError: x" }))).toBe("writing");
  });
  it("shows the bullets once written, a failure beside them only when there are none", () => {
    expect(outlookPhase(o({ bullets: [take], at: "2026-10-02T09:00-04:00" }))).toBe("ready");
    expect(outlookPhase(o({ bullets: [take], error: "RuntimeError: x" }))).toBe("ready");
    expect(outlookPhase(o({ error: "RuntimeError: Not logged in" }))).toBe("failed");
  });
});
