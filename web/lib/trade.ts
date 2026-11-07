import type { TradeHole, TradeMarket } from "./api";
import { SLOT_LABEL } from "./board";

export const marketValue = (n: number) => n.toLocaleString("en-US");

export const marketDelta = (n: number) => `${n < 0 ? "−" : "+"}${marketValue(Math.abs(n))}`;

// Only the players in the trade: each side goes from what it gives up to what it receives.
export function marketSides({ send, get }: TradeMarket) {
  return {
    me: { delta: get - send, before: send, after: get },
    them: { delta: send - get, before: get, after: send },
  };
}

export function tradePath(team: number, give: number[], get: number[]): string {
  const q = new URLSearchParams({ team: String(team) });
  give.forEach((id) => q.append("give", String(id)));
  get.forEach((id) => q.append("get", String(id)));
  return `/trade?${q}`;
}

// One line per slot the trade empties, its weeks folded into runs, since losing a starter empties the slot for weeks on
// end. Holes come in week order. A second empty slot in one week reads once: the points already count it.
export function holeLines(holes: TradeHole[]): string[] {
  const weeks = new Map<string, number[]>();
  for (const { week, slot } of holes) {
    const label = SLOT_LABEL[slot] ?? slot;
    const seen = weeks.get(label) ?? [];
    if (seen.at(-1) !== week) seen.push(week);
    weeks.set(label, seen);
  }
  return [...weeks].map(([slot, ws]) => {
    const runs: number[][] = [];
    for (const w of ws) {
      const run = runs.at(-1);
      if (run && run.at(-1) === w - 1) run.push(w);
      else runs.push([w]);
    }
    const text = runs.map((r) => (r.length > 1 ? `${r[0]}–${r.at(-1)}` : `${r[0]}`)).join(", ");
    return `${slot} empty ${ws.length > 1 ? "wks" : "wk"} ${text}`;
  });
}
