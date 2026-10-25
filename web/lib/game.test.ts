import { describe, expect, it } from "vitest";
import type { Player } from "./api";
import { gameOn, gamePhase, sideTotals, toPlay } from "./game";

const T0 = Date.parse("2026-09-13T17:00:00Z"); // Sunday 1 PM ET
const p = (over: Partial<Player>): Player => ({
  player_id: 1, name: "X", pos: "RB", team: "BAL", slot: "RB", bye: false, injury: "ACTIVE",
  proj: 10, spread: null, n_src: 2, proj_src: { espn: 10, sleeper: 10 }, actual: 0, game_played: 0,
  season_proj: 100, ros_proj: null, ros_spread: null, ros_n: null,
  return_week: null, injury_note: null, exp: null, ahead: [],
  opponent: "PIT", kickoff: "2026-09-13T13:00:00-04:00", home: true, ...over,
});

describe("gamePhase", () => {
  it("is pending before kickoff", () => {
    expect(gamePhase(p({}), T0 - 60_000)).toBe("pending");
  });
  it("is live once the kickoff clock has passed, even at zero points", () => {
    expect(gamePhase(p({}), T0)).toBe("live");
  });
  it("is live when points have posted before the clock says so", () => {
    expect(gamePhase(p({ actual: 0.5 }), T0 - 60_000)).toBe("live");
  });
  it("is final once ESPN marks the game played, points or none", () => {
    expect(gamePhase(p({ game_played: 100 }), T0 - 60_000)).toBe("final");
    expect(gamePhase(p({ game_played: 100, actual: 0 }), T0 + 4 * 3_600_000)).toBe("final");
  });
  it("stays live while the feed has the game on, though ESPN already calls it played", () => {
    const game = { status: "in" as const, quarter: 4, clock: "1:13", score: {} };
    expect(gamePhase(p({ game_played: 100, game }), T0 + 3.2 * 3_600_000)).toBe("live");
  });
  it("is final once the feed calls the game over, though ESPN has not marked it played yet", () => {
    const game = { status: "post" as const, quarter: 4, clock: "0:00", score: {} };
    expect(gamePhase(p({ actual: 5.4, game }), T0 + 3 * 3_600_000)).toBe("final");
  });
  it("a bye or an unscheduled player stays pending", () => {
    expect(gamePhase(p({ bye: true, kickoff: null }), T0 + 3_600_000)).toBe("pending");
    expect(gamePhase(p({ kickoff: null }), T0 + 3_600_000)).toBe("pending");
  });
  it("a bye stays pending though ESPN marks it played (game_played 100, all week)", () => {
    expect(gamePhase(p({ bye: true, kickoff: null, game_played: 100 }), T0 - 60_000)).toBe("pending");
  });
});

describe("toPlay", () => {
  const idle = (x: Player) => x.bye || x.injury === "OUT";
  it("counts starters whose game is not final, skipping idle ones", () => {
    const lineup = [
      p({ player_id: 1 }),
      p({ player_id: 2, game_played: 100, actual: 12 }),
      p({ player_id: 3, actual: 3 }),
      p({ player_id: 4, bye: true, kickoff: null }),
      p({ player_id: 5, injury: "OUT" }),
    ];
    expect(toPlay(lineup, idle, T0)).toBe(2);
  });
});

describe("sideTotals", () => {
  const idle = (x: Player) => x.bye || x.injury === "OUT";
  it("splits posted points from the projection still to come", () => {
    const lineup = [
      p({ player_id: 1, proj: 10.2 }),                                   // pending
      p({ player_id: 2, proj: 15.1, game_played: 100, actual: 0.5 }),   // final
      p({ player_id: 3, proj: 8, actual: 3.3 }),                         // live: 3.3 posted, 4.7 still to come
      p({ player_id: 4, proj: 9, bye: true, kickoff: null }),            // idle, adds nothing
      p({ player_id: 5, proj: 7, injury: "OUT" }),
    ];
    expect(sideTotals(lineup, idle, T0 - 60_000)).toEqual({ scored: 3.8, toCome: 14.9, estimate: 18.7 });
  });
  it("a player mid-game who has passed his projection adds nothing more to come", () => {
    expect(sideTotals([p({ proj: 8, actual: 11.5 })], idle, T0 - 60_000)).toEqual({ scored: 11.5, toCome: 0, estimate: 11.5 });
  });
  it("a player mid-game adds his projection times the share of the game left on the feed's clock", () => {
    const game = (quarter: number, clock: string) => ({ status: "in" as const, quarter, clock, score: {} });
    // 2nd quarter, 7:30 left: 2.5 of 4 quarters to play
    expect(sideTotals([p({ proj: 16, actual: 2, game: game(2, "7:30") })], idle, T0)).toEqual({ scored: 2, toCome: 10, estimate: 12 });
    expect(sideTotals([p({ proj: 16, actual: 9, game: game(5, "8:00") })], idle, T0)).toEqual({ scored: 9, toCome: 0, estimate: 9 });
  });
  it("a game the feed calls over adds nothing more, though ESPN has not marked it played", () => {
    const game = { status: "post" as const, quarter: 4, clock: "0:00", score: {} };
    expect(sideTotals([p({ proj: 18.3, actual: 5.42, game })], idle, T0 + 3 * 3_600_000)).toEqual({ scored: 5.42, toCome: 0, estimate: 5.42 });
  });
  it("is all projection before kickoff and all points once final", () => {
    expect(sideTotals([p({ proj: 10 })], idle, T0 - 1)).toEqual({ scored: 0, toCome: 10, estimate: 10 });
    expect(sideTotals([p({ proj: 10, game_played: 100, actual: 12 })], idle, T0)).toEqual({ scored: 12, toCome: 0, estimate: 12 });
  });
});

describe("gameOn", () => {
  const g = (status: "in" | "post") => ({ status, quarter: 2, clock: "5:00", score: {} });
  it("is on while a starter's game is in progress", () => {
    expect(gameOn([p({}), p({ game: g("in") })])).toBe(true);
  });
  it("is off before kickoff and after the final, whatever else is on", () => {
    expect(gameOn([p({}), p({ game: g("post") })])).toBe(false);
    expect(gameOn([])).toBe(false);
  });
});
