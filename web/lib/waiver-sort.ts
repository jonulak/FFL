import type { Form, WaiverRow } from "./api";
import { FORM_COLUMNS } from "./format";

export type Dir = "asc" | "desc";
export type Sort = { key: string; dir: Dir };

// What each sortable column of the Waivers table reads off a row; null sorts last either way.
const SORT_BY: Record<string, (r: WaiverRow) => number | string | null> = {
  name: (r) => r.name, add: (r) => r.add, wk: (r) => r.week_proj, ros: (r) => r.ros_adj, model: (r) => r.ros_proj,
  dr: (r) => r.delta_roster, dp: (r) => r.delta_pool,
  ...Object.fromEntries(FORM_COLUMNS.map((c) => [c.key, (r: WaiverRow) => (r.form ? (r.form[c.key as keyof Form] as number | null) : null)])),
};

export function sortRows(rows: WaiverRow[], sort: Sort): WaiverRow[] {
  const get = SORT_BY[sort.key];
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = get(a), y = get(b);
    if (x == null || y == null) return x == null && y == null ? 0 : x == null ? 1 : -1;
    return (x < y ? -1 : x > y ? 1 : 0) * sign;
  });
}
