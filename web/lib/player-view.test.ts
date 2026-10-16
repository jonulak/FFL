import { describe, expect, it } from "vitest";
import type { PlayerView } from "./api";
import { encodeFilter, filterLabel, isModified, playersHref } from "./player-view";

const P: PlayerView = { pos: "RB", fa: true, cols: ["add", "wk"], filters: [{ field: "snap_pct", op: "ge", value: 50 }], sort: { id: "add", desc: true } };

describe("playersHref", () => {
  it("the preset alone when nothing differs", () => expect(playersHref("", "waivers", P, P)).toBe("/players?preset=waivers"));
  it("only the fields that differ, fa as 1/0", () => {
    expect(playersHref("", "w", { ...P, pos: "WR", fa: false }, P)).toBe("/players?preset=w&pos=WR&fa=0");
    expect(playersHref("", "w", { ...P, fa: true }, { ...P, fa: false })).toBe("/players?preset=w&fa=1");
  });
  it("keeps the other overrides when the position changes", () => {
    const v = { ...P, cols: ["snap_pct"], sort: { id: "snap_pct", desc: true }, pos: "TE" };
    expect(playersHref("", "w", v, P)).toBe("/players?preset=w&pos=TE&cols=snap_pct&sort=snap_pct%3Adesc");
  });
  it("filters repeat as f=, cleared filters as a bare f=, no columns as a bare cols=", () => {
    const v = { ...P, filters: [{ field: "snap_pct", op: "ge" as const, value: 40 }, { field: "team" as const, op: "in" as const, value: ["DET", "GB"] }] };
    expect(playersHref("", "w", v, P)).toBe("/players?preset=w&f=snap_pct%3Age%3A40&f=team%3Ain%3ADET%2CGB");
    expect(playersHref("", "w", { ...P, filters: [] }, P)).toBe("/players?preset=w&f=");
    expect(playersHref("", "w", { ...P, cols: [] }, P)).toBe("/players?preset=w&cols=");
  });
  it("a trimmed query rides first", () => expect(playersHref("  st. brown ", "w", P, P)).toBe("/players?q=st.+brown&preset=w"));
});

describe("isModified", () => {
  it("same view is not; any field off is", () => {
    expect(isModified(structuredClone(P), P)).toBe(false);
    expect(isModified({ ...P, cols: ["wk", "add"] }, P)).toBe(true);
    expect(isModified({ ...P, sort: { id: "add", desc: false } }, P)).toBe(true);
  });
});

describe("filterLabel", () => {
  it("names a numeric filter by the column's heading, not its id", () => {
    expect(filterLabel({ field: "snap_pct", op: "ge", value: 50 })).toBe("snap ≥ 50");
    expect(filterLabel({ field: "rz_att", op: "le", value: 1.5 })).toBe("rz car ≤ 1.5");
  });
  it("set filters read their buckets", () => {
    expect(filterLabel({ field: "injury", op: "in", value: ["healthy", "qd"] })).toBe("injury: healthy, Q / D");
    expect(filterLabel({ field: "team", op: "in", value: ["DET", "GB"] })).toBe("team: DET, GB");
  });
});

describe("encodeFilter", () => {
  it("numeric and set", () => {
    expect(encodeFilter({ field: "rz_tgt", op: "le", value: 1.5 })).toBe("rz_tgt:le:1.5");
    expect(encodeFilter({ field: "injury", op: "in", value: ["healthy", "qd"] })).toBe("injury:in:healthy,qd");
  });
});
