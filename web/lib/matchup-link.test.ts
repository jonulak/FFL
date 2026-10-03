import { describe, expect, it } from "vitest";
import type { League } from "./api";
import { matchupHref, parseMatchupParams } from "./matchup-link";

const side = (team_id: number) => ({ team_id, team: `T${team_id}`, abbrev: "", logo: "", wins: 0, losses: 0, ties: 0, score: 0, proj: 0, me: false, starter_ids: [] });
const m: League["scoreboard"][number] = { away: side(3), home: side(4) };

describe("matchupHref", () => {
  it("links the current and past weeks by the away team", () => {
    expect(matchupHref(m, 5, 5)).toBe("/league/5/3");
    expect(matchupHref(m, 2, 5)).toBe("/league/2/3");
  });
  it("gives a future week no link: it has no lineups yet", () => {
    expect(matchupHref(m, 6, 5)).toBeNull();
  });
});

describe("parseMatchupParams", () => {
  it("reads two positive integers", () => {
    expect(parseMatchupParams("5", "3")).toEqual({ week: 5, team: 3 });
  });
  it("refuses anything else", () => {
    for (const [w, t] of [["0", "3"], ["5", "x"], ["1.5", "3"], ["5", "-2"]]) expect(parseMatchupParams(w, t)).toBeNull();
  });
});
