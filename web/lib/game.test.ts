import { describe, expect, it } from "vitest";
import type { Player } from "./api";
import { gamePhase, toPlay } from "./game";

const T0 = Date.parse("2026-09-13T17:00:00Z"); // Sunday 1 PM ET
const p = (over: Partial<Player>): Player => ({
  player_id: 1, name: "X", pos: "RB", team: "BAL", slot: "RB", bye: false, injury: "ACTIVE",
  proj_espn: 10, proj_sleeper: 10, proj: 10, spread: null, espn_only: false, actual: 0, game_played: 0,
  season_proj: 100, season_proj_sleeper: null, return_week: null, injury_note: null,
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
  it("a bye or an unscheduled player stays pending", () => {
    expect(gamePhase(p({ bye: true, kickoff: null }), T0 + 3_600_000)).toBe("pending");
    expect(gamePhase(p({ kickoff: null }), T0 + 3_600_000)).toBe("pending");
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
