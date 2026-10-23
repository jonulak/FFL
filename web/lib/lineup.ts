import type { Card } from "./api";
import { SLOT_LABEL } from "./board";

// ESPN's lineup order, as on its team page; season.py's SLOT_ORDER.
export const SLOT_ORDER = ["QB", "RB", "WR", "TE", "RB/WR/TE", "RB/WR", "WR/TE", "OP", "D/ST", "K"];
const FLEX = new Set(["RB/WR/TE", "RB/WR", "WR/TE"]);

export function slotLabel(slot: string): string {
  return slot === "BE" ? "Bench" : (SLOT_LABEL[slot] ?? slot);
}

// The Matchup board's starter rows: one per league slot (the payload's `slots`, season.starter_slots), each side's
// player in that slot or nobody, so an empty slot stays a blank half instead of pulling the rows under it up. A
// starter in a slot the list lacks follows the rest; a payload without `slots` (cached before it) pairs by position.
export function pairStarters<P extends { slot: string }>(slots: string[] | undefined, a: P[], b: P[]): [string, P | undefined, P | undefined][] {
  if (!slots?.length) {
    return Array.from({ length: Math.max(a.length, b.length) }, (_, i) => [(a[i] ?? b[i])!.slot, a[i], b[i]]);
  }
  const left = [[...a], [...b]];
  const take = (side: P[], slot: string) => { const i = side.findIndex((p) => p.slot === slot); return i < 0 ? undefined : side.splice(i, 1)[0]; };
  const rows: [string, P | undefined, P | undefined][] = slots.map((s) => [s, take(left[0], s), take(left[1], s)]);
  const [ra, rb] = left;
  for (let i = 0; i < Math.max(ra.length, rb.length); i++) rows.push([(ra[i] ?? rb[i])!.slot, ra[i], rb[i]]);
  return rows;
}

// Which positions a slot takes: season._slot_accepts. "OP" is the superflex.
export function accepts(slot: string): Set<string> {
  if (slot === "OP") return new Set(["QB", "RB", "WR", "TE"]);
  return new Set(FLEX.has(slot) ? slot.split("/") : [slot]);
}

// ESPN's lineup and the call disagree: the chip gets the accent. OUT/BYE/IR on the bench agree with it; a starter
// in another slot than the call's (STRATEGY.md, Slot by kickoff) does not.
export function isMismatch(c: Pick<Card, "group" | "call"> & Partial<Pick<Card, "slot" | "call_slot">>): boolean {
  if (c.group === "starter" && c.call === "START") return c.call_slot != null && c.slot !== c.call_slot;
  return c.group === "starter" || (c.group === "bench" && c.call === "START");
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

// His game has started, so ESPN will not move him: the roster build's `locked`, or the live overlay's `game` once
// his club has kicked off (fastcast.game_states: "in" or "post"), which lands before the next roster build does.
export const isLocked = (c: Pick<Card, "locked"> & Partial<Pick<Card, "game">>) =>
  c.locked || c.game?.status === "in" || c.game?.status === "post";

const fits = (p: Card, slot: string) =>
  slot === "BE" || (slot === "IR" ? p.slot === "IR" || irEligible(p) : accepts(slot).has(p.pos));
const starts = (slot: string) => slot !== "BE" && slot !== "IR";
// what a player is worth to the starting lineup in a slot
const worth = (c: Card, slot: string) => (starts(slot) ? c.play_proj : 0);
const round = (n: number) => Math.round(n * 100) / 100 || 0;  // never −0

// Where the picked player can go in one tap, with the change to the starting lineup's projection. A swap needs
// each player to fit the other's slot (IR takes only a player listed IR or Out); locked players are never
// offered; two bench players never swap.
export function targets(cards: Card[], picked: Card, empty: string[]): Target[] {
  if (isLocked(picked)) return [];
  const out: Target[] = [];
  for (const q of cards) {
    if (q.player_id === picked.player_id || isLocked(q) || q.slot === picked.slot) continue;
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

// Where the call wants him: the slot a START names (call_slot), the bench for any other call. IR is the manager's
// business, so a player there, or a START with no slot named, has no wanted slot.
export function wantedSlot(c: Pick<Card, "slot" | "call"> & Partial<Pick<Card, "call_slot">>): string | null {
  if (c.slot === "IR") return null;
  return c.call === "START" ? (c.call_slot ?? null) : "BE";
}

// The recommended move for a player out of his wanted slot: the one target that puts him there, preferring a partner
// it also puts where the call wants him (the starter he should replace, the bench man who should take his slot), then
// the better gain (the list is already in gain order). None for a player already in place.
export function recommendedTarget<T extends SwapOption>(picked: Card, options: T[]): T | null {
  const want = wantedSlot(picked);
  if (want == null || want === picked.slot) return null;
  let best: T | null = null, bestScore = 0;
  for (const o of options) {
    if (o.kind === "locked" || optionSlot(o) !== want) continue;
    const score = o.kind === "swap" && wantedSlot(o.with) === picked.slot ? 2 : 1;
    if (score > bestScore) [best, bestScore] = [o, score];
  }
  return best;
}

// An empty slot's recommended fill: the player the call puts in it who is not there yet.
export function recommendedFill<T extends FillOption>(slot: string, options: T[]): T | null {
  return options.find((o) => o.kind === "fill" && wantedSlot(o.who) === slot) ?? null;
}

// The swap sheet's rows: the recommended move first (recommendedTarget), then every target, best change to the
// lineup first (then ESPN's slot order, then name), and
// after them the partners who would be legal but whose game has started. Nothing for a locked player. While an IR
// player has no designation (irIllegal), anyone else is offered only the move that takes his place on IR.
export function swapOptions(cards: Card[], picked: Card, empty: string[]): SwapOption[] {
  if (isLocked(picked)) return [];
  const bad = irIllegal(cards);
  if (bad && bad.player_id !== picked.player_id) {
    return targets(cards, picked, []).filter((t) => t.kind === "swap" && t.with.player_id === bad.player_id);
  }
  const open: SwapOption[] = targets(cards, picked, empty).sort((a, b) => b.gain - a.gain || bySlotName(a, b));
  const best = recommendedTarget(picked, open);
  if (best) open.splice(0, 0, ...open.splice(open.indexOf(best), 1));
  const byId = new Map(cards.map((c) => [c.player_id, c]));
  const shut: SwapOption[] = targets(cards.map((c) => (isLocked(c) ? { ...c, locked: false, game: undefined } : c)), picked, [])
    .flatMap((t) => (t.kind === "swap" && isLocked(byId.get(t.with.player_id)!)
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
  const open: FillOption[] = fit.filter((c) => !isLocked(c))
    .map((c) => ({ kind: "fill" as const, who: c, gain: round(worth(c, slot) - worth(c, c.slot)) }))
    .sort((a, b) => b.gain - a.gain || byName(a.who, b.who));
  const best = recommendedFill(slot, open);
  if (best) open.splice(0, 0, ...open.splice(open.indexOf(best), 1));
  const shut: FillOption[] = fit.filter(isLocked).sort(byName).map((c) => ({ kind: "locked" as const, with: c }));
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

// season.reseat: a player moved into a starting slot takes the row (espn_order) of the one who left it, first in for
// first out by the order shown, so a swap trades rows instead of re-sorting by ESPN's acquisition order.
function reseat(cards: Card[], moves: Move[]): Map<number, number> {
  const byId = new Map(cards.map((c) => [c.player_id, c]));
  const order = (id: number) => byId.get(id)?.espn_order ?? 0;
  const out = new Map<number, number>();
  for (const slot of new Set(moves.map((m) => m.to_slot).filter(starts))) {
    const leaving = moves.filter((m) => byId.get(m.player_id)?.slot === slot).map((m) => m.player_id).sort((a, b) => order(a) - order(b));
    const entering = moves.filter((m) => m.to_slot === slot).map((m) => m.player_id).sort((a, b) => order(a) - order(b));
    entering.forEach((id, i) => { if (i < leaving.length) out.set(id, order(leaving[i])); });
  }
  return out;
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
  const seat = reseat(r.cards, moves);
  const cards = r.cards.map((c) => {
    const slot = to.get(c.player_id);
    if (slot === undefined || slot === c.slot) return c;
    return { ...c, slot, group: groupOf(slot), espn_order: seat.get(c.player_id) ?? c.espn_order };
  }).sort(cardOrder);
  const filled = new Map<string, number>();
  for (const c of cards) if (sized(c.slot)) add(filled, c.slot);
  const slots = [...SLOT_ORDER, ...[...size.keys()].filter((s) => !SLOT_ORDER.includes(s))];
  const empty = slots.flatMap((s) => Array<string>(Math.max(0, (size.get(s) ?? 0) - (filled.get(s) ?? 0))).fill(s));
  return { ...r, cards, empty_slots: empty };
}

// A tap still saving, a lineup move or a sit. RosterLive posts them one at a time in tap order (a later move may
// fill the slot an earlier one emptied) and holds only their own players meanwhile.
export type Op = { key: number; kind: "move"; moves: Move[] } | { key: number; kind: "sit"; id: number; sat: boolean };
export const opIds = (o: Op): number[] => (o.kind === "move" ? o.moves.map((m) => m.player_id) : [o.id]);

// His card once a sit lands, drawn at the tap: a START or BENCH reads SIT; stood back up, he gets the call he would
// have had.
export function sitCard(c: Card, sat: boolean): Card {
  const call = sat ? (c.call === "START" || c.call === "BENCH" ? "SIT" : c.call) : (c.call === "SIT" ? c.model_call : c.call);
  return { ...c, sat, call };
}

// The page with every op still saving laid over the last answer: a read-back or a live refetch replaces the answer
// and the ops still out lay over it again. An op is a no-op on a page that already has it.
export function applyOps<T extends { cards: Card[]; empty_slots: string[] }>(r: T, ops: Op[]): T {
  return ops.reduce((acc, o) => (o.kind === "move" ? applyMoves(acc, o.moves)
    : { ...acc, cards: acc.cards.map((c) => (c.player_id === o.id && c.sat !== o.sat ? sitCard(c, o.sat) : c)) }), r);
}

// A sheet's options without the players whose own move or sit is still saving.
export function withoutBusy<T extends SwapOption | FillOption>(options: T[], busy: Set<number>): T[] {
  return options.filter((o) => {
    const id = o.kind === "fill" ? o.who.player_id : o.kind === "swap" || o.kind === "locked" ? o.with.player_id : null;
    return id == null || !busy.has(id);
  });
}

// The page after a refused move: as the last answer has it, unless ESPN probably took the move (`moved`: only the
// answer or the read-back failed), when the moved rows are the better guess and the notice asks for a reload.
export function afterFailure<T extends { cards: Card[]; empty_slots: string[] }>(f: Pick<LineupFailure, "moved">, r: T, moves: Move[]): T {
  return f.moved ? applyMoves(r, moves) : r;
}

// A target's change to the starting lineup's projection: "+2.00", "−1.00" (a true minus), "±0" for no change.
export function gainText(n: number): string {
  const r = Math.round(n * 100) / 100;
  if (r === 0) return "±0";
  return r > 0 ? `+${r.toFixed(2)}` : `−${Math.abs(r).toFixed(2)}`;
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
