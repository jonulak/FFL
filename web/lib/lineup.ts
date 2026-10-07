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
    if (fits(picked, slot)) out.push({ kind: "slot", slot, gain: starts(picked.slot) ? 0 : round(picked.play_proj) });
  }
  if (starts(picked.slot)) out.push({ kind: "bench", gain: round(-picked.play_proj) });
  return out;
}

export function movesFor(picked: Card, t: Target): { player_id: number; to_slot: string }[] {
  if (t.kind === "bench") return [{ player_id: picked.player_id, to_slot: "BE" }];
  if (t.kind === "slot") return [{ player_id: picked.player_id, to_slot: t.slot }];
  return [{ player_id: picked.player_id, to_slot: t.with.slot }, { player_id: t.with.player_id, to_slot: picked.slot }];
}
