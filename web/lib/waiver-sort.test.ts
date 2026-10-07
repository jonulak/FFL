import { createTable, getCoreRowModel, getSortedRowModel, type SortingState, type TableOptionsResolved } from "@tanstack/react-table";
import { describe, expect, it } from "vitest";
import type { PlayerRow } from "./api";
import { SORT_COLUMNS } from "./waiver-sort";

const row = (name: string, add: number | null, form: Partial<PlayerRow["form"]> | null, ros = 0) =>
  ({ name, add, week_proj: 0, ros_adj: ros, form } as unknown as PlayerRow);

// The headless table the component builds, sorted by one column: the names in order.
function sorted(rows: PlayerRow[], sorting: SortingState): string[] {
  const options: TableOptionsResolved<PlayerRow> = {
    data: rows, columns: Object.values(SORT_COLUMNS), state: { sorting }, onStateChange: () => {},
    renderFallbackValue: null, getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
  };
  const table = createTable(options);
  table.setOptions((o) => ({ ...o, state: { ...table.initialState, sorting } }));
  return table.getRowModel().rows.map((r) => r.original.name);
}

describe("Players table sorting", () => {
  it("sorts by the adjusted ROS", () => {
    const rows = [row("A", 1, null, 50), row("B", 2, null, 20), row("C", 3, null, 90)];
    expect(sorted(rows, [{ id: "ros", desc: true }])).toEqual(["C", "A", "B"]);
  });
  it("puts a missing add last both ways (my own player has none)", () => {
    const rows = [row("a", null, null), row("b", -3, null), row("c", 4, null)];
    expect(sorted(rows, [{ id: "add", desc: true }])).toEqual(["c", "b", "a"]);
    expect(sorted(rows, [{ id: "add", desc: false }])).toEqual(["b", "c", "a"]);
  });
  it("sorts a form column, a player without form last both ways", () => {
    const rows = [row("A", 0, null), row("B", 0, { snap_pct: 40 }), row("C", 0, { snap_pct: 80 })];
    expect(sorted(rows, [{ id: "snap_pct", desc: true }])).toEqual(["C", "B", "A"]);
    expect(sorted(rows, [{ id: "snap_pct", desc: false }])).toEqual(["B", "C", "A"]);
  });
  it("sorts names A to Z", () => {
    expect(sorted([row("C", 0, null), row("A", 0, null), row("B", 0, null)], [{ id: "name", desc: false }])).toEqual(["A", "B", "C"]);
  });
  it("numbers open high first, the name A to Z", () => {
    expect(SORT_COLUMNS.add.sortDescFirst).toBe(true);
    expect(SORT_COLUMNS.name.sortDescFirst).toBe(false);
  });
});
