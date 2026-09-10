import type { Player } from "./api";

// Where a player's game stands, which decides whether his number is a forecast or a fact.
// ESPN's `game_played` is coarse (0 until three hours after kickoff, then 100), so a game
// in progress is read from the kickoff clock, or from points that have already posted.
export type Phase = "pending" | "live" | "final";

export function gamePhase(p: Pick<Player, "game_played" | "actual" | "kickoff" | "bye">, now: number): Phase {
  if (p.game_played >= 100) return "final";
  if (p.actual > 0) return "live";
  if (!p.bye && p.kickoff && new Date(p.kickoff).getTime() <= now) return "live";
  return "pending";
}

// Starters whose game can still add points: not final, and not idle for the week.
export function toPlay(lineup: Player[], idle: (p: Player) => boolean, now: number): number {
  return lineup.filter((p) => !idle(p) && gamePhase(p, now) !== "final").length;
}

// The side's total in three parts: points already posted (final and in-progress games), the
// projection still to come, and their sum as the estimate. A player mid-game is carried at his
// projection until his posted score passes it: what he has posted is scored, the rest of his
// projection is still to come. ESPN gives no game-progress figure to scale by, so this is the
// honest middle between counting him at zero remaining and at his full projection.
export function sideTotals(lineup: Player[], idle: (p: Player) => boolean, now: number) {
  let scored = 0, toCome = 0;
  for (const p of lineup) {
    const phase = gamePhase(p, now);
    if (phase === "final") scored += p.actual;
    else if (phase === "live") { scored += p.actual; toCome += Math.max(0, p.proj - p.actual); }
    else if (!idle(p)) toCome += p.proj;
  }
  const r = (n: number) => Math.round(n * 10) / 10;
  return { scored: r(scored), toCome: r(toCome), estimate: r(scored + toCome) };
}
