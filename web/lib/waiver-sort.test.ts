import { describe, expect, it } from "vitest";
import type { WaiverRow } from "./api";
import { sortRows } from "./waiver-sort";

const row = (name: string, add: number, form: Partial<WaiverRow["form"]> | null) =>
  ({ name, add, week_proj: 0, ros_proj: 0, delta_roster: 0, delta_pool: 0, form } as unknown as WaiverRow);

describe("sortRows", () => {
  const rows = [row("B", 1, { snap_pct: 40 }), row("A", 3, null), row("C", 2, { snap_pct: 90 })];
  it("orders numbers either way and never mutates the input", () => {
    expect(sortRows(rows, { key: "add", dir: "desc" }).map((r) => r.name)).toEqual(["A", "C", "B"]);
    expect(sortRows(rows, { key: "add", dir: "asc" }).map((r) => r.name)).toEqual(["B", "C", "A"]);
    expect(rows.map((r) => r.name)).toEqual(["B", "A", "C"]);
  });
  it("puts a player without the number last in both directions", () => {
    expect(sortRows(rows, { key: "snap_pct", dir: "desc" }).map((r) => r.name)).toEqual(["C", "B", "A"]);
    expect(sortRows(rows, { key: "snap_pct", dir: "asc" }).map((r) => r.name)).toEqual(["B", "C", "A"]);
  });
  it("sorts names alphabetically", () => {
    expect(sortRows(rows, { key: "name", dir: "asc" }).map((r) => r.name)).toEqual(["A", "B", "C"]);
  });
});
