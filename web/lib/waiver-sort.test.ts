import { describe, expect, it } from "vitest";
import type { PlayerRow } from "./api";
import { sortRows } from "./waiver-sort";

const row = (name: string, add: number, form: Partial<PlayerRow["form"]> | null, ros: [number, number] = [0, 0]) =>
  ({ name, add, week_proj: 0, ros_proj: ros[0], ros_adj: ros[1], delta_roster: 0, delta_pool: 0, form } as unknown as PlayerRow);

describe("sortRows", () => {
  const rows = [row("B", 1, { snap_pct: 40 }, [100, 90]), row("A", 3, null, [120, 120]), row("C", 2, { snap_pct: 90 }, [80, 130])];
  it("sorts the ROS column by the adjusted number and the model by its own", () => {
    expect(sortRows(rows, { key: "ros", dir: "desc" }).map((r) => r.name)).toEqual(["C", "A", "B"]);
    expect(sortRows(rows, { key: "model", dir: "desc" }).map((r) => r.name)).toEqual(["A", "B", "C"]);
  });
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

describe("sortRows on the Players table", () => {
  it("a null add (my own player) sorts last both ways", () => {
    const rows = [{ name: "a", add: null }, { name: "b", add: 2 }, { name: "c", add: 5 }] as unknown as PlayerRow[];
    expect(sortRows(rows, { key: "add", dir: "desc" }).map((r) => r.name)).toEqual(["c", "b", "a"]);
    expect(sortRows(rows, { key: "add", dir: "asc" }).map((r) => r.name)).toEqual(["b", "c", "a"]);
  });
});
