import type { Card } from "./api";

// ESPN's lineup order, as on its team page; season.py's SLOT_ORDER.
export const SLOT_ORDER = ["QB", "RB", "WR", "TE", "RB/WR/TE", "RB/WR", "WR/TE", "OP", "D/ST", "K"];
const FLEX = new Set(["RB/WR/TE", "RB/WR", "WR/TE"]);

export function slotLabel(slot: string): string {
  return FLEX.has(slot) ? "FLEX" : slot === "BE" ? "Bench" : slot;
}

// Which positions a slot takes: season._slot_accepts. "OP" is the superflex.
export function accepts(slot: string): Set<string> {
  if (slot === "OP") return new Set(["QB", "RB", "WR", "TE"]);
  return new Set(FLEX.has(slot) ? slot.split("/") : [slot]);
}

// ESPN's lineup and the call disagree: the chip gets the accent. OUT/BYE/IR on the bench agree with it.
export function isMismatch(c: Pick<Card, "group" | "call">): boolean {
  return (c.group === "starter" && c.call !== "START") || (c.group === "bench" && c.call === "START");
}

export type Target = { kind: "swap"; with: Card; gain: number } | { kind: "slot"; slot: string; gain: number } | { kind: "bench"; gain: number };

const fits = (p: Card, slot: string) => slot === "BE" || accepts(slot).has(p.pos);
const starts = (slot: string) => slot !== "BE" && slot !== "IR";
const round = (n: number) => Math.round(n * 10) / 10;

// Where the picked player can go in one tap, with the change to the starting lineup's projection. A swap needs
// each player to fit the other's slot; locked and IR players are never offered; two bench players never swap.
export function targets(cards: Card[], picked: Card, empty: string[]): Target[] {
  if (picked.locked || picked.slot === "IR") return [];
  const out: Target[] = [];
  for (const q of cards) {
    if (q.player_id === picked.player_id || q.locked || q.slot === "IR" || q.slot === picked.slot) continue;
    if (!starts(q.slot) && !starts(picked.slot)) continue;
    if (!fits(picked, q.slot) || !fits(q, picked.slot)) continue;
    const gain = starts(picked.slot) === starts(q.slot) ? 0
      : starts(q.slot) ? picked.play_proj - q.play_proj : q.play_proj - picked.play_proj;
    out.push({ kind: "swap", with: q, gain: round(gain) });
  }
  for (const slot of new Set(empty)) {
    if (slot !== picked.slot && fits(picked, slot)) out.push({ kind: "slot", slot, gain: starts(picked.slot) ? 0 : round(picked.play_proj) });
  }
  if (starts(picked.slot)) out.push({ kind: "bench", gain: round(-picked.play_proj) });
  return out;
}

export function movesFor(picked: Card, t: Target): { player_id: number; to_slot: string }[] {
  if (t.kind === "bench") return [{ player_id: picked.player_id, to_slot: "BE" }];
  if (t.kind === "slot") return [{ player_id: picked.player_id, to_slot: t.slot }];
  return [{ player_id: picked.player_id, to_slot: t.with.slot }, { player_id: t.with.player_id, to_slot: picked.slot }];
}

// One row of the swap sheet: a move it can make, or a partner whose game has started (shown, not tappable).
export type SwapOption = Target | { kind: "locked"; with: Card };

// ESPN's slot order, the bench after it.
const slotRank = (slot: string) => { const i = SLOT_ORDER.indexOf(slot); return i < 0 ? SLOT_ORDER.length : i; };
const optionSlot = (o: SwapOption) => (o.kind === "bench" ? "BE" : o.kind === "slot" ? o.slot : o.with.slot);
const optionName = (o: SwapOption) => (o.kind === "swap" || o.kind === "locked" ? o.with.name : "");
const bySlotName = (a: SwapOption, b: SwapOption) =>
  slotRank(optionSlot(a)) - slotRank(optionSlot(b)) || optionName(a).localeCompare(optionName(b));

// The swap sheet's rows: every target, best change to the lineup first (then ESPN's slot order, then name), and
// after them the partners who would be legal but whose game has started. Nothing for a locked or IR player.
export function swapOptions(cards: Card[], picked: Card, empty: string[]): SwapOption[] {
  if (picked.locked || picked.slot === "IR") return [];
  const open: SwapOption[] = targets(cards, picked, empty).sort((a, b) => b.gain - a.gain || bySlotName(a, b));
  const byId = new Map(cards.map((c) => [c.player_id, c]));
  const shut: SwapOption[] = targets(cards.map((c) => (c.locked ? { ...c, locked: false } : c)), picked, [])
    .flatMap((t) => (t.kind === "swap" && byId.get(t.with.player_id)?.locked
      ? [{ kind: "locked" as const, with: byId.get(t.with.player_id)! }] : []))
    .sort(bySlotName);
  return [...open, ...shut];
}

// A target's change to the starting lineup's projection: "+2.0", "−1.0" (a true minus), "±0" for no change.
export function gainText(n: number): string {
  const r = Math.round(n * 10) / 10;
  if (r === 0) return "±0";
  return r > 0 ? `+${r.toFixed(1)}` : `−${Math.abs(r).toFixed(1)}`;
}

// A refused POST /lineup, as the row says it. `moved`: ESPN took the move but the read-back failed, so the
// page is behind, not wrong; `reload` offers the button that fetches the page again.
export type LineupFailure = { text: string; reload: boolean; moved: boolean };

export function lineupFailure(status: number | null, body: unknown, message: string): LineupFailure {
  const detail = (body as { detail?: unknown } | null)?.detail;
  const o = detail && typeof detail === "object" && !Array.isArray(detail)
    ? (detail as { errors?: unknown; error?: unknown; moved?: unknown }) : null;
  if (o?.moved === true) return { text: "Moved in ESPN — reload to see it", reload: true, moved: true };
  if (o && Array.isArray(o.errors) && o.errors.length) return { text: o.errors.map(String).join("; "), reload: false, moved: false };
  if (o && typeof o.error === "string" && o.error) {
    const text = status === 502 && !/^ESPN/.test(o.error) ? `ESPN: ${o.error}` : o.error;
    return { text, reload: status === 409 && /week changed/i.test(o.error), moved: false };
  }
  if (typeof detail === "string" && detail) return { text: detail, reload: false, moved: false };
  return { text: `Not moved: ${message}`, reload: false, moved: false };
}
