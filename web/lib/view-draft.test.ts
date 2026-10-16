import { describe, expect, it } from "vitest";
import { columnOrder, toggleColumn, moveColumn, pickedColumns, setFilter, removeFilter, finishedFilters, presetView, needsRostered } from "./view-draft";

describe("the Columns list", () => {
  it("lists the picked columns in order, then the rest in catalogue order", () => {
    const order = columnOrder(["snap_pct", "add"]);
    expect(order.slice(0, 3)).toEqual([{ id: "snap_pct", on: true }, { id: "add", on: true }, { id: "wk", on: false }]);
    expect(order.length).toBe(21);
  });
  it("a tick flips the row in place; the columns are the ticked rows top to bottom", () => {
    const list = columnOrder(["add", "wk"]);
    const ids = list.map((c) => c.id);
    const snap = toggleColumn(list, "snap_pct");
    expect(snap.map((c) => c.id)).toEqual(ids);
    expect(pickedColumns(snap)).toEqual(["add", "wk", "snap_pct"]);
    const off = toggleColumn(snap, "add");
    expect(off.map((c) => c.id)).toEqual(ids);
    expect(pickedColumns(off)).toEqual(["wk", "snap_pct"]);
    expect(pickedColumns(toggleColumn(off, "add"))).toEqual(["add", "wk", "snap_pct"]);
  });
  it("a drag moves a ticked row; an unticked row stays put", () => {
    const list = columnOrder(["add", "wk", "ros"]);
    expect(pickedColumns(moveColumn(list, "ros", "add"))).toEqual(["ros", "add", "wk"]);
    expect(moveColumn(list, "exp", "add")).toBe(list);
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

describe("needsRostered", () => {
  it("is true for an avail filter naming theirs or mine, false otherwise", () => {
    expect(needsRostered([{ field: "avail", op: "in", value: ["fa", "theirs"] }])).toBe(true);
    expect(needsRostered([{ field: "avail", op: "in", value: ["mine"] }])).toBe(true);
    expect(needsRostered([{ field: "avail", op: "in", value: ["fa", "waivers"] }])).toBe(false);
    expect(needsRostered([{ field: "injury", op: "in", value: ["out"] }])).toBe(false);
    expect(needsRostered([])).toBe(false);
  });
});
