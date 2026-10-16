import { describe, expect, it } from "vitest";
import { columnOrder, toggleColumn, moveColumn, setFilter, removeFilter, finishedFilters, presetView } from "./view-draft";

describe("the Columns list", () => {
  it("lists the picked columns in order, then the rest in catalogue order", () => {
    const order = columnOrder(["snap_pct", "add"]);
    expect(order.slice(0, 3)).toEqual([{ id: "snap_pct", on: true }, { id: "add", on: true }, { id: "wk", on: false }]);
    expect(order.length).toBe(21);
  });
  it("checking a column appends it to the picked ones; unchecking drops it", () => {
    expect(toggleColumn(["add"], "wk")).toEqual(["add", "wk"]);
    expect(toggleColumn(["add", "wk"], "add")).toEqual(["wk"]);
  });
  it("a drag reorders the picked columns by the list's ids; an unchecked row dragged among them is ignored", () => {
    const list = columnOrder(["add", "wk", "ros"]).map((c) => c.id);
    expect(moveColumn(["add", "wk", "ros"], list, "ros", "add")).toEqual(["ros", "add", "wk"]);
    expect(moveColumn(["add", "wk"], columnOrder(["add", "wk"]).map((c) => c.id), "exp", "add")).toEqual(["add", "wk"]);
  });
});

describe("the Filters list", () => {
  it("one filter per field: setting a field again replaces it, in place", () => {
    const a = setFilter([], { field: "snap_pct", op: "ge", value: 50 });
    const b = setFilter(a, { field: "team", op: "in", value: ["DET"] });
    expect(setFilter(b, { field: "snap_pct", op: "le", value: 20 })).toEqual([{ field: "snap_pct", op: "le", value: 20 }, { field: "team", op: "in", value: ["DET"] }]);
    expect(removeFilter(b, "snap_pct")).toEqual([{ field: "team", op: "in", value: ["DET"] }]);
  });
  it("Apply drops a numeric row with no number and a team row with no club", () => {
    const rows = [
      { field: "snap_pct", op: "ge" as const, value: NaN },
      { field: "rz_tgt", op: "le" as const, value: 0 },
      { field: "team" as const, op: "in" as const, value: [] },
      { field: "injury" as const, op: "in" as const, value: ["healthy"] },
    ];
    expect(finishedFilters(rows)).toEqual([rows[1], rows[3]]);
  });
});

describe("presetView", () => {
  it("drops the id and the name", () => {
    const p = { id: "w", name: "Waivers", pos: "RB", fa: true, cols: ["add"], filters: [], sort: { id: "add", desc: true } };
    expect(presetView(p)).toEqual({ pos: "RB", fa: true, cols: ["add"], filters: [], sort: { id: "add", desc: true } });
  });
});
