import type { Decision, DecisionRef, Presets } from "./api";

export const KIND_LABEL: Record<string, string> = { lineup: "Lineup", sit: "Sit", unsit: "Unsit", claims: "Claims",
  free_add: "Add", trade: "Trade", draft: "Draft", note: "Note" };

export const needsRationale = (d: Pick<Decision, "rationale">) => d.rationale == null;

export function reasonText(d: Pick<Decision, "kind" | "rationale" | "rationale_note">, presets: Presets): string | null {
  if (d.rationale == null) return null;
  const labels = d.rationale.filter((k) => k !== "other").map((k) => presets[d.kind]?.find((p) => p.key === k)?.label ?? k);
  return [...labels, d.rationale_note].filter(Boolean).join(" — ") || null;
}

// The form's own check, the server's rule (decisions.validate_rationale) mirrored so Save says why before a round trip.
// A reason is required; the Decisions page's Edit may instead record an outcome alone (pass `outcome` there).
export function formError(key: string | null, note: string, outcome?: string | null): string | null {
  if (key == null && !outcome?.trim()) return "Pick a reason";
  if (key === "other" && !note.trim()) return "Say why under Other";
  return null;
}

// Whether the form's reason differs from the one stored, compared as the server stores it (first key, trimmed note).
// An unchanged reason is not sent, so a stored one today's rule would refuse (an imported Other with no note) still
// takes an outcome.
export function changedReason(initial: Partial<Pick<Decision, "rationale" | "rationale_note">>, key: string | null, note: string): boolean {
  return key !== (initial.rationale?.[0] ?? null) || note.trim() !== (initial.rationale_note ?? "").trim();
}

// What a move's answer asks of the page: the dialog for a reason, a toast for one recorded as the call, or nothing
// (a dev API, or a record the DB refused: the move stands either way).
export function askFor(ref: DecisionRef | null | undefined): "dialog" | "toast" | null {
  if (!ref) return null;
  return ref.needs_rationale ? "dialog" : "toast";
}

// Which of a claims submit's rows the one reason form writes: the first that still needs a reason is the form's own
// row, and only the others that need one get the copy, so a resubmit never overwrites a reason already given.
export function claimsWhyTargets(refs: DecisionRef[]): { first: DecisionRef; rest: DecisionRef[] } | null {
  const [first, ...rest] = refs.filter((r) => r.needs_rationale);
  return first ? { first, rest } : null;
}

// What the claims form says once the reason is saved: the claims row's own save stands, and a free-add row whose
// copy failed is named so "Saved" is not read as all of them.
export function savedNote(failedCopies: number): string {
  if (failedCopies === 0) return "Saved";
  return `Saved. ${failedCopies} add${failedCopies === 1 ? " still needs" : "s still need"} a reason (Decisions page)`;
}

// Newest week first; rows with no week (draft-era, notes) last under "Season".
export function byWeek(rows: Decision[]): { week: number | null; rows: Decision[] }[] {
  const groups = new Map<number | null, Decision[]>();
  for (const r of rows) groups.set(r.week, [...(groups.get(r.week) ?? []), r]);
  return [...groups.entries()].sort(([a], [b]) => (b ?? -1) - (a ?? -1)).map(([week, rows]) => ({ week, rows }));
}
