import { describe, expect, it } from "vitest";
import { decide, leagueOf, tabsFor, withLeague } from "./league";

const PUB = "espn:950458410";

describe("withLeague", () => {
  it("leaves a link alone with no league (home)", () => {
    expect(withLeague("/roster", null)).toBe("/roster");
    expect(withLeague("/players?pos=WR", "")).toBe("/players?pos=WR");
  });
  it("adds the league as the first or a further parameter", () => {
    expect(withLeague("/roster", PUB)).toBe(`/roster?league=${encodeURIComponent(PUB)}`);
    expect(withLeague("/players?pos=WR&f=", PUB)).toBe(`/players?pos=WR&f=&league=${encodeURIComponent(PUB)}`);
  });
  it("replaces a league already there and keeps a hash", () => {
    expect(withLeague("/players?league=espn%3A1&pos=QB#top", PUB)).toBe(`/players?pos=QB&league=${encodeURIComponent(PUB)}#top`);
  });
});

describe("leagueOf", () => {
  it("reads the league off a query string, none or empty as home", () => {
    expect(leagueOf(`?league=${encodeURIComponent(PUB)}&pos=WR`)).toBe(PUB);
    expect(leagueOf("?pos=WR")).toBeNull();
    expect(leagueOf("?league=")).toBeNull();
    expect(leagueOf("")).toBeNull();
  });
});

describe("decide (the proxy)", () => {
  it("opens the start page on the last league used", () => {
    expect(decide("/", "", PUB)).toEqual({ redirect: `/roster?league=${encodeURIComponent(PUB)}` });
    expect(decide("/", "", null)).toEqual({});
    expect(decide("/", "?week=3", null)).toEqual({});
  });
  it("a league page remembers its league, and home when it names none", () => {
    expect(decide("/roster", `?league=${PUB}`, null)).toEqual({ cookie: PUB });
    expect(decide("/players", `?league=${PUB}&pos=WR`, PUB)).toEqual({}); // already remembered
    expect(decide("/roster", "", PUB)).toEqual({ cookie: null });
    expect(decide("/team/3", "", null)).toEqual({});
  });
  it("a global page (NFL, News, Settings) leaves the last league alone", () => {
    for (const p of ["/nfl", "/nfl/8/DET", "/news", "/news/player/1", "/settings", "/plays"]) expect(decide(p, "", PUB)).toEqual({});
  });
});

describe("tabsFor", () => {
  const tabs = [["/", "Matchup"], ["/roster", "Roster"], ["/nfl", "NFL"], ["/players", "Players"]] as const;
  it("home opens every tab; another league only its pages, each carrying the league", () => {
    expect(tabsFor(tabs, null, null)).toEqual(tabs.map(([h, l]) => [h, l, h]));
    expect(tabsFor(tabs, ["roster", "players"], PUB)).toEqual([
      ["/roster", "Roster", withLeague("/roster", PUB)], ["/players", "Players", withLeague("/players", PUB)]]);
  });
});
