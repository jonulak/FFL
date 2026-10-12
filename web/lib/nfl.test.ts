import { describe, expect, it } from "vitest";
import type { NflDrive, NflGame, NflPlay } from "./api";
import { feedHref, filterDrives, gameHref, gameLinkFor,gameState, inGameRows, parseFilter, parseGameParams, priorScores, scoreLine, windowLabel } from "./nfl";

const play = (o: Partial<NflPlay>): NflPlay => ({ id: "1", quarter: 1, clock: "1:00", team: "GB", down: null, text: "", kind: null,
  scoring: false, turnover: false, penalty: false, score: { away: 0, home: 7 }, athletes: [], mine: [], opp: [], ...o });
const club = (abbr: string, score: number | null = null) => ({ abbr, id: abbr, name: null, location: null, record: null, score, winner: false });
const game = (o: Partial<NflGame>): NflGame => ({ event_id: "e", kickoff: "2026-10-11T17:00Z", status: "in", detail: null, quarter: 2, clock: "4:12",
  network: null, away: club("CHI", 0), home: club("GB", 7), ball: null, red_zone: false, down: null, mine: 0, opp: 0, ...o });

describe("nfl links", () => {
  it("builds game and feed hrefs", () => {
    expect(gameHref(5, "GB")).toBe("/nfl/5/GB");
    expect(feedHref(5, "GB")).toBe("/nfl/5/GB");
    expect(feedHref(5, "GB", "mine")).toBe("/nfl/5/GB?f=mine");
    expect(feedHref(5, "GB", "all")).toBe("/nfl/5/GB");
  });
  it("parses params and filters", () => {
    expect(parseGameParams("5", "gb")).toEqual({ week: 5, team: "GB" });
    expect(parseGameParams("0", "GB")).toBeNull();
    expect(parseGameParams("5", "G1")).toBeNull();
    expect(parseFilter("scoring")).toBe("scoring");
    expect(parseFilter(["mine"])).toBe("mine");
    expect(parseFilter("junk")).toBe("all");
    expect(parseFilter(undefined)).toBe("all");
  });
});

describe("filterDrives", () => {
  const drives: NflDrive[] = [
    { id: "2", team: "GB", description: "", result: "TD", plays: [play({ id: "3", scoring: true }), play({ id: "2", mine: ["X"] })] },
    { id: "1", team: "CHI", description: "", result: "PUNT", plays: [play({ id: "1" })] },
  ];
  it("keeps everything for all", () => expect(filterDrives(drives, "all")).toBe(drives));
  it("drops drives emptied by the filter", () => {
    expect(filterDrives(drives, "scoring").map((d) => d.id)).toEqual(["2"]);
    expect(filterDrives(drives, "scoring")[0].plays.map((p) => p.id)).toEqual(["3"]);
    expect(filterDrives(drives, "mine")[0].plays.map((p) => p.id)).toEqual(["2"]);
  });
  it("keeps only my starters' plays for My players; the opponent's stay under All", () => {
    expect(filterDrives([{ ...drives[1], plays: [play({ opp: ["Y"] })] }], "mine")).toEqual([]);
    expect(filterDrives([{ ...drives[1], plays: [play({ opp: ["Y"], mine: ["X"] })] }], "mine")).toHaveLength(1);
  });
  it("is empty when nothing survives", () => expect(filterDrives([drives[1]], "mine")).toEqual([]));
});

describe("labels", () => {
  it("reads the score away then home, and says whose part is the scorer's", () => {
    expect(scoreLine(play({ team: "GB", score: { away: 0, home: 7 } }), game({}))).toEqual({ away: "CHI 0", home: "GB 7", scorer: "home" });
    expect(scoreLine(play({ team: "CHI", score: { away: 3, home: 7 } }), game({}))).toEqual({ away: "CHI 3", home: "GB 7", scorer: "away" });
    expect(scoreLine(play({ team: null, score: { away: 3, home: 7 } }), game({}))).toEqual({ away: "CHI 3", home: "GB 7", scorer: null });
  });
  it("gives the score to the side whose score rose, not the offence (pick-six)", () => {
    const prev = { away: 7, home: 0 };
    expect(scoreLine(play({ team: "CHI", score: { away: 7, home: 7 } }), game({}), prev).scorer).toBe("home");
    expect(scoreLine(play({ team: "GB", score: { away: 9, home: 0 } }), game({}), prev).scorer).toBe("away"); // safety
    expect(scoreLine(play({ team: "GB", score: { away: 7, home: 0 } }), game({}), prev).scorer).toBe("home"); // unreadable: offence
  });
  it("pairs each scoring play with the one before it", () => {
    const a = play({ id: "3", scoring: true, score: { away: 7, home: 7 } });
    const b = play({ id: "2", scoring: true, score: { away: 7, home: 0 } });
    expect(priorScores([a, play({ id: "x", scoring: false }), b])).toEqual({ "3": b.score, "2": null });
  });
  it("names the game state", () => {
    expect(gameState(game({}))).toBe("Q2 4:12");
    expect(gameState(game({ detail: "Halftime", clock: "0:00" }))).toBe("HALF");
    expect(gameState(game({ quarter: 5, clock: "3:10" }))).toBe("OT 3:10");
    expect(gameState(game({ status: "post", quarter: 4 }))).toBe("FINAL");
    expect(gameState(game({ status: "post", quarter: 5 }))).toBe("FINAL/OT");
  });
  it("reads postponed and delayed games verbatim", () => {
    expect(gameState(game({ status: "post", detail: "Postponed" }))).toBe("POSTPONED");
    expect(gameState(game({ status: "in", detail: "Delayed" }))).toBe("DELAYED");
  });
  it("names kickoff windows", () => {
    expect(windowLabel("2026-10-11T17:00Z", "America/New_York")).toBe("SUN 1:00 PM");
    expect(windowLabel("2026-10-12T00:20Z", "America/New_York")).toBe("SNF");
    expect(windowLabel("2026-10-13T00:15Z", "America/New_York")).toBe("MNF");
    expect(windowLabel("2026-10-09T00:15Z", "America/New_York")).toBe("TNF");
  });
});

describe("inGameRows", () => {
  const s = (name: string) => ({ player_id: 1, name, pos: "WR", slot: "WR", team: "GB", actual: 0, game_played: 0 });
  it("pairs mine with the opponent's, the shorter side empty", () => {
    expect(inGameRows({ me: [s("A"), s("B")], opp: [s("Z")] }).map(([a, b]) => [a?.name ?? null, b?.name ?? null]))
      .toEqual([["A", "Z"], ["B", null]]);
    expect(inGameRows({ me: [], opp: [] })).toEqual([]);
  });
});

describe("gameLinkFor", () => {
  it("links a player with a game to his club's game in that week", () => {
    expect(gameLinkFor({ bye: false, opponent: "CHI", team: "GB" }, 5)).toBe("/nfl/5/GB");
  });
  it("does not link a bye or a player without a game", () => {
    expect(gameLinkFor({ bye: true, opponent: null, team: "KC" }, 5)).toBeNull();
    expect(gameLinkFor({ bye: false, opponent: null, team: "FA" }, 5)).toBeNull();
  });
});
