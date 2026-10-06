import { describe, expect, it } from "vitest";
import { isFreeAgent, ownerLabel, playersHref, rowKind } from "./players";
import type { PlayerRow } from "./api";

describe("playersHref", () => {
  it("query and tab, fa on omitted", () => {
    expect(playersHref("st. brown", "WR", true)).toBe("/players?q=st.+brown&pos=WR");
    expect(playersHref("  gibbs ", "D/ST", true)).toBe("/players?q=gibbs&pos=D%2FST");
  });
  it("blank query is the tab alone", () => expect(playersHref("   ", "RB", true)).toBe("/players?pos=RB"));
  it("fa off rides as fa=0", () => expect(playersHref("", "K", false)).toBe("/players?pos=K&fa=0"));
});

describe("rowKind", () => {
  it("free agent, another team's, mine", () => {
    expect(rowKind({ owner: null, mine: false })).toBe("free");
    expect(rowKind({ owner: { team_id: 2, name: "Them", abbrev: "THM" }, mine: false })).toBe("theirs");
    expect(rowKind({ owner: { team_id: 7, name: "Mine", abbrev: null }, mine: true })).toBe("mine");
  });
  it("isFreeAgent narrows on owner", () => {
    const rows = [{ owner: null, mine: false, add: 3 }, { owner: { team_id: 2, name: null, abbrev: null }, mine: false, add: 1 }] as PlayerRow[];
    expect(rows.filter(isFreeAgent).length).toBe(1);
  });
});

describe("ownerLabel", () => {
  it("abbrev, then name, then rostered", () => {
    expect(ownerLabel({ team_id: 2, name: "Them", abbrev: "THM" })).toBe("THM");
    expect(ownerLabel({ team_id: 2, name: "Them", abbrev: null })).toBe("Them");
    expect(ownerLabel({ team_id: 2, name: null, abbrev: null })).toBe("rostered");
  });
});
