import type { Player } from "./api";

// Where a player's game stands, which decides whether his number is a forecast or a fact.
// ESPN's `game_played` is coarse (0 until three hours after kickoff, then 100, whether or not
// the game has ended), so the live feed's status wins over it, in progress or over; otherwise a
// game in progress is read from the kickoff clock, or from points that have already posted. A bye
// has no game, though ESPN marks it played (100) all week: left as "final" it read as a kickoff.
export type Phase = "pending" | "live" | "final";

export function gamePhase(p: Pick<Player, "game_played" | "actual" | "kickoff" | "bye"> & Partial<Pick<Player, "game">>, now: number): Phase {
  if (p.game?.status === "in") return "live";
  if (p.game?.status === "post") return "final";
  if (p.bye) return "pending";
  if (p.game_played >= 100) return "final";
  if (p.actual > 0) return "live";
  if (!p.bye && p.kickoff && new Date(p.kickoff).getTime() <= now) return "live";
  return "pending";
}

// Starters whose game can still add points: not final, and not idle for the week.
export function toPlay(lineup: Player[], idle: (p: Player) => boolean, now: number): number {
  return lineup.filter((p) => !idle(p) && gamePhase(p, now) !== "final").length;
}

// The share of a live player's game still to play, off the feed's quarter and clock (overtime
// counts as none left); null when the feed has no clock for him. Mirrors season.game_left, which
// the win meter prices on.
export function gameLeft(game: Player["game"] | undefined): number | null {
  if (game?.status !== "in" || !game.quarter) return null;
  if (game.quarter > 4) return 0;
  const [m, s] = (game.clock ?? "").split(":").map(Number);
  const mins = Number.isFinite(m) && Number.isFinite(s) ? m + s / 60 : 0;
  return Math.max(0, Math.min(1, (4 - game.quarter + mins / 15) / 4));
}

// The side's total in three parts: points already posted (final and in-progress games), the
// projection still to come, and their sum as the estimate. A player mid-game adds his projection
// times the share of his game left (the win meter's expected final, season.side_outlook); with no
// clock to read (the feed is down) he is carried at his projection until his posted score passes it.
// A League tile's starters (ScoreStarter) carry just enough of a Player for it.
type Totalled = Pick<Player, "proj" | "actual" | "game_played" | "kickoff" | "bye"> & Partial<Pick<Player, "game">>;
export function sideTotals<P extends Totalled>(lineup: P[], idle: (p: P) => boolean, now: number) {
  let scored = 0, toCome = 0;
  for (const p of lineup) {
    const phase = gamePhase(p, now);
    if (phase === "final") scored += p.actual;
    else if (phase === "live") {
      scored += p.actual;
      const left = gameLeft(p.game);
      toCome += left === null ? Math.max(0, p.proj - p.actual) : p.proj * left;
    }
    else if (!idle(p)) toCome += p.proj;
  }
  const r = (n: number) => Math.round(n * 100) / 100;
  return { scored: r(scored), toCome: r(toCome), estimate: r(scored + toCome) };
}

// A board is live while one of its starters' games is in progress (the overlay's `game`, off
// the feed), not merely while some game in the league is on. The same rule marks a League tile.
export function gameOn(lineup: Pick<Player, "game">[]): boolean {
  return lineup.some((p) => p.game?.status === "in");
}
