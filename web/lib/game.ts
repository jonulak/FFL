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
