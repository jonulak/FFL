// Win probability as the board says it: whole percent, and never 100 or 0 while a game can
// still turn, so a lopsided side reads ">99%" until the whistle.
export function pct(p: number): string {
  if (p >= 0.995) return ">99%";
  if (p <= 0.005) return "<1%";
  return `${Math.round(p * 100)}%`;
}

// A finished game's odds are the result: 1 won, 0 lost, anything between a tie.
export function outcome(p: number): "W" | "L" | "T" {
  return p >= 1 ? "W" : p <= 0 ? "L" : "T";
}

// The bar fills from its centre toward the favourite's end by the margin over even, in percent of
// the bar's width: 71% fills 21 toward the left, a final the whole half to the winner, an even game
// nothing. `left` is where the fill starts. An even game counts as the left side's for `end`.
export function fill(p: number): { left: number; width: number; end: "left" | "right" } {
  const width = Math.round(Math.abs(p - 0.5) * 1000) / 10;
  return p >= 0.5 ? { left: 50 - width, width, end: "left" } : { left: 50, width, end: "right" };
}

// A finished game's odds off the two scores as the page shows them (to the hundredth, so float
// noise in a sum never splits a tie): 1 the left side won, 0 it lost, 0.5 a tie.
export function finalP(left: number, right: number): number {
  const d = Math.round((left - right) * 100);
  return d > 0 ? 1 : d < 0 ? 0 : 0.5;
}

type Odds = { abbrev: string; p: number };

// The side the odds favour with its share; a matchup the rounding calls even says so.
export function favourite(a: Odds, b: Odds): string {
  if (Math.round(a.p * 100) === Math.round(b.p * 100)) return "50–50";
  const f = a.p > b.p ? a : b;
  return `${f.abbrev} ${pct(f.p)}`;
}
