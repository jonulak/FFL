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

// The View sheet's edits, pure (ffl-8iz): the Columns list is every column, the picked ones first in
// their order, the rest after in catalogue order; a drag reorders the picked ones; one filter per field.
export function columnOrder(cols: string[]): { id: string; on: boolean }[] {
  return [...cols.map((id) => ({ id, on: true })), ...COLUMN_IDS.filter((id) => !cols.includes(id)).map((id) => ({ id, on: false }))];
}
export function toggleColumn(cols: string[], id: string): string[] {
  return cols.includes(id) ? cols.filter((c) => c !== id) : [...cols, id];
}
export function moveColumn(cols: string[], listIds: string[], activeId: string, overId: string): string[] {
  const from = listIds.indexOf(activeId), to = listIds.indexOf(overId);
  if (from < 0 || to < 0 || !cols.includes(activeId)) return cols;
  return arrayMove(listIds, from, to).filter((id) => cols.includes(id));
}
export function setFilter(filters: ViewFilter[], f: ViewFilter): ViewFilter[] {
  return filters.some((x) => x.field === f.field) ? filters.map((x) => (x.field === f.field ? f : x)) : [...filters, f];
}
export function removeFilter(filters: ViewFilter[], field: string): ViewFilter[] {
  return filters.filter((x) => x.field !== field);
}
