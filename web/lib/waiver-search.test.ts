import { describe, expect, it } from "vitest";
import { waiverHref } from "./waiver-search";

describe("waiverHref", () => {
  it("keeps the tab beside a query, so clearing returns to it", () => {
    expect(waiverHref("st. brown", "WR")).toBe("/waivers?q=st.+brown&pos=WR");
    expect(waiverHref("  gibbs ", "D/ST")).toBe("/waivers?q=gibbs&pos=D%2FST");
  });
  it("a blank query is the tab alone", () => {
    expect(waiverHref("   ", "RB")).toBe("/waivers?pos=RB");
    expect(waiverHref("", "K")).toBe("/waivers?pos=K");
  });
});
