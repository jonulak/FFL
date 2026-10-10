import type { Decision, Presets } from "./api";

export const KIND_LABEL: Record<string, string> = { lineup: "Lineup", sit: "Sit", unsit: "Unsit", claims: "Claims",
  free_add: "Add", trade: "Trade", draft: "Draft", note: "Note" };

export const needsRationale = (d: Pick<Decision, "rationale">) => d.rationale == null;

export function reasonText(d: Pick<Decision, "kind" | "rationale" | "rationale_note">, presets: Presets): string | null {
  if (d.rationale == null) return null;
  const labels = d.rationale.filter((k) => k !== "other").map((k) => presets[d.kind]?.find((p) => p.key === k)?.label ?? k);
  return [...labels, d.rationale_note].filter(Boolean).join(" — ") || null;
}

// The form's own check, the server's rule (decisions.validate_rationale) mirrored so Save says why before a round trip.
export function formError(key: string | null, note: string): string | null {
  if (key === "other" && !note.trim()) return "Say why under Other";
  return null;
}

// Newest week first; rows with no week (draft-era, notes) last under "Season".
export function byWeek(rows: Decision[]): { week: number | null; rows: Decision[] }[] {
  const groups = new Map<number | null, Decision[]>();
  for (const r of rows) groups.set(r.week, [...(groups.get(r.week) ?? []), r]);
  return [...groups.entries()].sort(([a], [b]) => (b ?? -1) - (a ?? -1)).map(([week, rows]) => ({ week, rows }));
}
