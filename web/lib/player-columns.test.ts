import { createTable, getCoreRowModel, getSortedRowModel, type SortingState, type TableOptionsResolved } from "@tanstack/react-table";
import { describe, expect, it } from "vitest";
import type { PlayerRow } from "./api";
import { COLUMN_IDS, PLAYER_COLUMNS, SORT_COLUMNS, sortFor } from "./player-columns";

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

describe("the column catalogue", () => {
  it("mirrors ffl/player_views.py COLUMNS, in order", () => {
    expect(COLUMN_IDS).toEqual(["add", "wk", "ros", "ros_model", "ros_spread", "d_roster", "d_pool", "l3", "beat", "pace_gap", "opp",
      "snap_pct", "tgt_snap", "ypt", "weight", "air_share", "air_catch", "rz_tgt", "rz_att", "rz_pass", "exp"]);
  });
  it("reads top-level and form figures", () => {
    const r = { add: 2, week_proj: 9, ros_adj: 80, ros_proj: 70, ros_spread: 6, delta_roster: 1, delta_pool: 3, exp: 0,
      form: { snap_pct: 55, rz_tgt: 1.3 } } as unknown as PlayerRow;
    const get = (id: string) => PLAYER_COLUMNS.find((c) => c.id === id)!.get(r);
    expect([get("add"), get("wk"), get("ros"), get("ros_model"), get("ros_spread"), get("d_roster"), get("d_pool"), get("exp"), get("snap_pct"), get("rz_tgt"), get("ypt")])
      .toEqual([2, 9, 80, 70, 6, 1, 3, 0, 55, 1.3, undefined]);
  });
});

describe("sortFor", () => {
  it("keeps a shown column or the name", () => {
    expect(sortFor(["wk"], { id: "wk", desc: false })).toEqual({ id: "wk", desc: false });
    expect(sortFor([], { id: "name", desc: false })).toEqual({ id: "name", desc: false });
  });
  it("a hidden sort column falls to the first shown, high first; none shown, the name", () => {
    expect(sortFor(["snap_pct", "wk"], { id: "add", desc: true })).toEqual({ id: "snap_pct", desc: true });
    expect(sortFor([], { id: "add", desc: true })).toEqual({ id: "name", desc: false });
  });
});
