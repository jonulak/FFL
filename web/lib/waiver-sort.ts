import type { ColumnDef } from "@tanstack/react-table";
import type { Form, PlayerRow } from "./api";

// What each sortable column of the Players table reads off a row, as TanStack column defs without
// their cells (the table adds those). A missing number is `undefined`, which TanStack sorts last
// whichever way the column runs; every number sorts high first, the name A to Z.
const num = (id: string, get: (r: PlayerRow) => number | null | undefined): ColumnDef<PlayerRow> => ({
  id, accessorFn: (r) => get(r) ?? undefined, sortDescFirst: true, sortUndefined: "last",
});
const form = (key: keyof Form) => num(key, (r) => (r.form ? (r.form[key] as number | null) : null));

export const SORT_COLUMNS = {
  name: { id: "name", accessorFn: (r) => r.name, sortDescFirst: false, sortingFn: "text" } as ColumnDef<PlayerRow>,
  add: num("add", (r) => r.add),
  wk: num("wk", (r) => r.week_proj),
  ros: num("ros", (r) => r.ros_adj),
  beat: form("beat"),
  pace_gap: form("pace_gap"),
  opp: form("opp"),
  snap_pct: form("snap_pct"),
};
