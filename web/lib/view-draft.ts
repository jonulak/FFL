import { arrayMove } from "@dnd-kit/sortable";
import type { PlayerView, Preset, ViewFilter } from "./api";
import { COLUMN_IDS } from "./player-columns";

// A saved preset as a view: what `go` takes to land on `?preset=<id>` alone.
export function presetView(p: Preset): PlayerView {
  return { pos: p.pos, fa: p.fa, cols: p.cols, filters: p.filters, sort: p.sort };
}

// The draft's filters as the API takes them: a numeric row with no number yet and a team row with no
// club are still being written, so Apply leaves them out rather than refusing.
export function finishedFilters(filters: ViewFilter[]): ViewFilter[] {
  return filters.filter((f) => (f.op === "in" ? f.value.length > 0 : Number.isFinite(f.value)));
}

// An Availability filter naming theirs or mine matches rostered players only, so the view must read
// the rostered half (fa false).
export function needsRostered(filters: ViewFilter[]): boolean {
  return filters.some((f) => f.field === "avail" && f.op === "in" && f.value.some((v) => v === "theirs" || v === "mine"));
}

// The View sheet's edits, pure (ffl-8iz): the Columns list is every column, opening with the picked ones
// first in their order and the rest after in catalogue order. From then on a row stays where it is: a
// tick only flips it (a row that jumped to its new place took the sheet's scroll with it), and only a
// drag of a picked row moves one. The view's columns are the ticked rows, top to bottom. One filter per field.
export type ColumnItem = { id: string; on: boolean };
export function columnOrder(cols: string[]): ColumnItem[] {
  return [...cols.map((id) => ({ id, on: true })), ...COLUMN_IDS.filter((id) => !cols.includes(id)).map((id) => ({ id, on: false }))];
}
export function toggleColumn(list: ColumnItem[], id: string): ColumnItem[] {
  return list.map((c) => (c.id === id ? { id, on: !c.on } : c));
}
export function moveColumn(list: ColumnItem[], activeId: string, overId: string): ColumnItem[] {
  const from = list.findIndex((c) => c.id === activeId), to = list.findIndex((c) => c.id === overId);
  if (from < 0 || to < 0 || !list[from].on) return list;
  return arrayMove(list, from, to);
}
export const pickedColumns = (list: ColumnItem[]): string[] => list.filter((c) => c.on).map((c) => c.id);
export function setFilter(filters: ViewFilter[], f: ViewFilter): ViewFilter[] {
  return filters.some((x) => x.field === f.field) ? filters.map((x) => (x.field === f.field ? f : x)) : [...filters, f];
}
export function removeFilter(filters: ViewFilter[], field: string): ViewFilter[] {
  return filters.filter((x) => x.field !== field);
}
