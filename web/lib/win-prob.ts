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

type Odds = { abbrev: string; p: number };

// The side the odds favour with its share; a matchup the rounding calls even says so.
export function favourite(a: Odds, b: Odds): string {
  if (Math.round(a.p * 100) === Math.round(b.p * 100)) return "50–50";
  const f = a.p > b.p ? a : b;
  return `${f.abbrev} ${pct(f.p)}`;
}
