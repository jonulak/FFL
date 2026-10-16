import { describe, expect, it } from "vitest";
import type { League } from "./api";
import { boardHref, matchupHref, parseMatchupParams, parseTeam, parseWeek, playsHref } from "./matchup-link";

const side = (team_id: number) => ({ team_id, team: `T${team_id}`, owner: "", abbrev: "", logo: "", wins: 0, losses: 0, ties: 0, score: 0, proj: 0, me: false, starter_ids: [], starter_teams: [], pending_teams: [], starters: [] });
const m: League["scoreboard"][number] = { away: side(3), home: side(4), live: false, final: false };

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

describe("plays links", () => {
  it("sends a board to its own week's plays: mine plain, any other game by its left team", () => {
    expect(playsHref(true, 7, 5)).toBe("/plays?week=5");
    expect(playsHref(false, 3, 5)).toBe("/plays?week=5&team=3");
  });
  it("leads back to the board the plays came from", () => {
    expect(boardHref(true, 6, 7, true)).toBe("/");
    expect(boardHref(true, 5, 7, false)).toBe("/league/5/7");
    expect(boardHref(false, 5, 3, true)).toBe("/league/5/3");
    expect(boardHref(false, null, 3, true)).toBe("/");
  });
  it("reads ?week= as a positive integer or nothing", () => {
    expect(parseWeek("5")).toBe(5);
    for (const raw of [undefined, "", "x", "0", "2.5"]) expect(parseWeek(raw)).toBeNull();
  });
  it("reads ?team= as a positive integer or nothing", () => {
    expect(parseTeam("4")).toBe(4);
    expect(parseTeam(["4", "9"])).toBe(4);
    for (const raw of [undefined, "", "x", "0", "-1", "2.5"]) expect(parseTeam(raw)).toBeNull();
  });
});
