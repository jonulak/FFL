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

// The league's IR rule (season.IR_ENTRY / NO_DESIGNATION): only a player listed IR or Out may go INTO IR; one already
// there may stay while he carries any designation. With none the roster is illegal and ESPN allows no other move.
export const IR_ENTRY = new Set(["INJURY_RESERVE", "OUT"]);
export const NO_DESIGNATION = new Set(["ACTIVE", "NORMAL", ""]);
export const irEligible = (c: Pick<Card, "injury">) => IR_ENTRY.has(c.injury ?? "");

// The IR player with no injury designation, if any (season.ir_illegal): every other move waits on him.
export function irIllegal(cards: Card[]): Card | null {
  return cards.find((c) => c.slot === "IR" && NO_DESIGNATION.has(c.injury ?? "")) ?? null;
}

const fits = (p: Card, slot: string) =>
  slot === "BE" || (slot === "IR" ? p.slot === "IR" || irEligible(p) : accepts(slot).has(p.pos));
const starts = (slot: string) => slot !== "BE" && slot !== "IR";
// what a player is worth to the starting lineup in a slot
const worth = (c: Card, slot: string) => (starts(slot) ? c.play_proj : 0);
const round = (n: number) => Math.round(n * 10) / 10 || 0;  // never −0

// Where the picked player can go in one tap, with the change to the starting lineup's projection. A swap needs
// each player to fit the other's slot (IR takes only a player listed IR or Out); locked players are never
// offered; two bench players never swap.
export function targets(cards: Card[], picked: Card, empty: string[]): Target[] {
  if (picked.locked) return [];
  const out: Target[] = [];
  for (const q of cards) {
    if (q.player_id === picked.player_id || q.locked || q.slot === picked.slot) continue;
    if (q.slot === "BE" && picked.slot === "BE") continue;
    if (!fits(picked, q.slot) || !fits(q, picked.slot)) continue;
    const gain = worth(picked, q.slot) + worth(q, picked.slot) - worth(picked, picked.slot) - worth(q, q.slot);
    out.push({ kind: "swap", with: q, gain: round(gain) });
  }
  for (const slot of new Set(empty)) {
    if (slot !== picked.slot && fits(picked, slot)) out.push({ kind: "slot", slot, gain: round(worth(picked, slot) - worth(picked, picked.slot)) });
  }
  if (picked.slot !== "BE") out.push({ kind: "bench", gain: round(-worth(picked, picked.slot)) });
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
// after them the partners who would be legal but whose game has started. Nothing for a locked player. While an IR
// player has no designation (irIllegal), anyone else is offered only the move that takes his place on IR.
export function swapOptions(cards: Card[], picked: Card, empty: string[]): SwapOption[] {
  if (picked.locked) return [];
  const bad = irIllegal(cards);
  if (bad && bad.player_id !== picked.player_id) {
    return targets(cards, picked, []).filter((t) => t.kind === "swap" && t.with.player_id === bad.player_id);
  }
  const open: SwapOption[] = targets(cards, picked, empty).sort((a, b) => b.gain - a.gain || bySlotName(a, b));
  const byId = new Map(cards.map((c) => [c.player_id, c]));
  const shut: SwapOption[] = targets(cards.map((c) => (c.locked ? { ...c, locked: false } : c)), picked, [])
    .flatMap((t) => (t.kind === "swap" && byId.get(t.with.player_id)?.locked
      ? [{ kind: "locked" as const, with: byId.get(t.with.player_id)! }] : []))
    .sort(bySlotName);
  return [...open, ...shut];
}

// An empty slot's sheet: everyone who could fill it. Bench and IR players add their projection to a starting slot;
// a starter in another slot moves over and leaves that one empty instead (no change). An empty IR takes only a
// player listed IR or Out. Anyone already in a slot of this type is left out; a player whose game has started is
// listed after, not tappable. While an IR player has no designation (irIllegal), only he is offered.
export type Fill = { kind: "fill"; who: Card; gain: number };
export type FillOption = Fill | { kind: "locked"; with: Card };

export function fillOptions(cards: Card[], slot: string): FillOption[] {
  const bad = irIllegal(cards);
  const fit = cards.filter((c) => c.slot !== slot && fits(c, slot) && (!bad || c.player_id === bad.player_id));
  const byName = (a: Card, b: Card) => a.name.localeCompare(b.name);
  const open: FillOption[] = fit.filter((c) => !c.locked)
    .map((c) => ({ kind: "fill" as const, who: c, gain: round(worth(c, slot) - worth(c, c.slot)) }))
    .sort((a, b) => b.gain - a.gain || byName(a.who, b.who));
  const shut: FillOption[] = fit.filter((c) => c.locked).sort(byName).map((c) => ({ kind: "locked" as const, with: c }));
  return [...open, ...shut];
}

export type Move = { player_id: number; to_slot: string };

const groupOf = (slot: string): Card["group"] => (slot === "BE" ? "bench" : slot === "IR" ? "ir" : "starter");

// season._card_sort_key: group; starters by slot, then ESPN's own order (`espn_order`); the rest by position, then
// projection, best first, then name (by code point, as Python compares). The optimistic page must order rows exactly
// as ESPN's read-back will, or a player who lands in the other of two FLEX rows is moved again when the answer arrives.
const GROUP_ORDER: Card["group"][] = ["starter", "bench", "ir"];
const POS_ORDER = ["QB", "RB", "WR", "TE", "D/ST", "K"];
const rankIn = (list: string[], s: string) => { const i = list.indexOf(s); return i < 0 ? list.length : i; };
function cardOrder(a: Card, b: Card): number {
  const rank = (c: Card) => (c.group === "starter" ? slotRank(c.slot) : rankIn(POS_ORDER, c.pos));
  const head = GROUP_ORDER.indexOf(a.group) - GROUP_ORDER.indexOf(b.group) || rank(a) - rank(b);
  if (head || a.group === "starter") return head || a.espn_order - b.espn_order;
  return (b.proj ?? 0) - (a.proj ?? 0) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
}

// The page as it will be once ESPN takes `moves`, drawn before it answers: the moved cards re-slotted (their group
// follows the slot) and the empty slots (starting and IR, as season.empty_slots) recounted against the lineup's own
// size, which is every such slot's players plus its empties before the move. The POST's answer replaces it.
export function applyMoves<T extends { cards: Card[]; empty_slots: string[] }>(r: T, moves: Move[]): T {
  const size = new Map<string, number>();
  const add = (m: Map<string, number>, s: string) => m.set(s, (m.get(s) ?? 0) + 1);
  const sized = (s: string) => s !== "BE";
  for (const c of r.cards) if (sized(c.slot)) add(size, c.slot);
  for (const s of r.empty_slots) add(size, s);
  const to = new Map(moves.map((m) => [m.player_id, m.to_slot]));
  const cards = r.cards.map((c) => {
    const slot = to.get(c.player_id);
    return slot === undefined || slot === c.slot ? c : { ...c, slot, group: groupOf(slot) };
  }).sort(cardOrder);
  const filled = new Map<string, number>();
  for (const c of cards) if (sized(c.slot)) add(filled, c.slot);
  const slots = [...SLOT_ORDER, ...[...size.keys()].filter((s) => !SLOT_ORDER.includes(s))];
  const empty = slots.flatMap((s) => Array<string>(Math.max(0, (size.get(s) ?? 0) - (filled.get(s) ?? 0))).fill(s));
  return { ...r, cards, empty_slots: empty };
}

// The page after a refused move: back as it was, unless ESPN probably took it (`moved`: only the answer or the
// read-back failed), when the optimistic rows are the better guess and the notice asks for a reload to confirm.
export function afterFailure<T>(f: Pick<LineupFailure, "moved">, before: T, optimistic: T): T {
  return f.moved ? optimistic : before;
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
  if (o?.moved === true) return { text: "Moved in ESPN — reload to confirm", reload: true, moved: true };
  if (o && Array.isArray(o.errors) && o.errors.length) return { text: o.errors.map(String).join("; "), reload: false, moved: false };
  if (o && typeof o.error === "string" && o.error) {
    const text = status === 502 && !/^ESPN/.test(o.error) ? `ESPN: ${o.error}` : o.error;
    return { text, reload: status === 409 && /week changed/i.test(o.error), moved: false };
  }
  if (typeof detail === "string" && detail) return { text: detail, reload: false, moved: false };
  return { text: `Not moved: ${message}`, reload: false, moved: false };
}
