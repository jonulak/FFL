import type { PlayerView, ViewFilter, ViewSort } from "./api";
import { columnSpec } from "./player-columns";

// The Players view in the URL (ffl-8iz; ffl/player_views.py parses the same params): the preset always,
// then only the fields the view has changed from it, so `?preset=x` alone is the saved view and anything
// more is "modified". fa rides as 1/0; filters repeat as f=field:op:value, a bare f= clears a preset's
// filters; cols= empty is a table with no picked columns.
export const ALL = "ALL";
export const POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K", ALL];
export const VIEW_FIELDS = ["pos", "fa", "cols", "filters", "sort"] as const;

// The set filters' buckets, as the View sheet names them.
export const FILTER_SETS = {
  injury: [["healthy", "healthy"], ["qd", "Q / D"], ["out", "out / IR"]],
  avail: [["fa", "free agent"], ["waivers", "on waivers"], ["theirs", "another team's"], ["mine", "mine"]],
} as const;

export const encodeFilter = (f: ViewFilter): string => (f.op === "in" ? `${f.field}:in:${f.value.join(",")}` : `${f.field}:${f.op}:${f.value}`);
export const encodeSort = (s: ViewSort): string => `${s.id}:${s.desc ? "desc" : "asc"}`;
// Objects compare by JSON, so key order matters: filters are {field, op, value} and sorts {id, desc}, as the API returns them.
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

export function isModified(view: PlayerView, preset: PlayerView): boolean {
  return VIEW_FIELDS.some((k) => !same(view[k], preset[k]));
}

export function playersHref(q: string, presetId: string, view: PlayerView, preset: PlayerView): string {
  const p = new URLSearchParams();
  if (q.trim()) p.set("q", q.trim());
  p.set("preset", presetId);
  if (view.pos !== preset.pos) p.set("pos", view.pos);
  if (view.fa !== preset.fa) p.set("fa", view.fa ? "1" : "0");
  if (!same(view.cols, preset.cols)) p.set("cols", view.cols.join(","));
  if (!same(view.filters, preset.filters)) {
    if (view.filters.length === 0) p.set("f", "");
    for (const f of view.filters) p.append("f", encodeFilter(f));
  }
  if (!same(view.sort, preset.sort)) p.set("sort", encodeSort(view.sort));
  return `/players?${p}`;
}

// A filter as a chip reads it: "snap ≥ 50", "injury: healthy, Q / D", "team: DET, GB".
export function filterLabel(f: ViewFilter): string {
  if (f.op === "in") {
    const names = f.field === "team" ? null : new Map<string, string>(FILTER_SETS[f.field]);
    return `${f.field}: ${f.value.map((v) => names?.get(v) ?? v).join(", ")}`;
  }
  return `${columnSpec(f.field)?.label ?? f.field} ${f.op === "ge" ? "≥" : "≤"} ${f.value}`;
}
