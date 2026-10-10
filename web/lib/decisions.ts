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

// Whether a move followed the app's call, worked out at the tap so the reason dialog (or the toast) opens before ESPN
// answers. The server records by the same rule (ffl/decisions.py followed_lineup / followed_sit); both run
// tests/fixtures/followed_call.json. A START lands in a starting slot, anything else off them.
const BENCH = new Set(["BE", "IR"]);

export function followedLineup(moves: { player_id: number; to_slot: string }[], cards: { player_id: number; call: string }[]): boolean {
  const call = new Map(cards.map((c) => [c.player_id, c.call]));
  return moves.every((m) => (call.get(m.player_id) === "START") === !BENCH.has(m.to_slot));
}

// Sitting a player the call starts goes against it; standing one back up, or sitting a bench player, does not.
export function followedSit(card: { call: string; model_call?: string | null }, sat: boolean): boolean {
  return !sat || (card.model_call || card.call) !== "START";
}

// A promise settled from outside: the reason form opens at the tap holding one, and the move's answer hands it the
// rows to save to (resolve), or the move failed (reject).
export function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void, reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  promise.catch(() => {}); // a move that fails before anyone saved is not an unhandled rejection
  return { promise, resolve, reject };
}

// Which of a claims submit's rows the one reason form writes (the first takes the form, the rest the copy). Only those
// still needing a reason, so a resubmit never overwrites one already given, and the form leaves once the answer says
// none does (`needed`). A reason saved before the answer then still lands, on every row of the submit.
export function whyRows(refs: DecisionRef[]): { ids: number[]; needed: boolean } {
  const open = refs.filter((r) => r.needs_rationale);
  return open.length ? { ids: open.map((r) => r.id), needed: true } : { ids: refs.map((r) => r.id), needed: false };
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
