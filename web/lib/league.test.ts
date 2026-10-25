import { describe, expect, it } from "vitest";
import { decide, leagueOf, START_URL, tabsFor, withLeague } from "./league";

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
  const to = `/roster?league=${encodeURIComponent(PUB)}`;
  it("opens the start page on the last league used", () => {
    expect(decide("/", "", PUB, true)).toEqual({ redirect: to });
    expect(decide("/", "", null, true)).toEqual({});
    expect(decide("/", "?week=3", null, true)).toEqual({});
  });
  it("the Home Screen launch (the manifest's start_url) opens the last league used, and forgets nothing", () => {
    expect(decide(START_URL.split("?")[0], `?${START_URL.split("?")[1]}`, PUB, true)).toEqual({ redirect: to });
    expect(decide(START_URL.split("?")[0], `?${START_URL.split("?")[1]}`, null, true)).toEqual({});
  });
  it("a league page remembers its league, and home when it names none", () => {
    expect(decide("/roster", `?league=${PUB}`, null, true)).toEqual({ cookie: PUB });
    expect(decide("/players", `?league=${PUB}&pos=WR`, PUB, true)).toEqual({}); // already remembered
    expect(decide("/roster", "", PUB, true)).toEqual({ cookie: null });
    expect(decide("/team/3", "", null, true)).toEqual({});
  });
  it("only a full page load counts: a prefetch or soft navigation neither redirects nor moves the last league", () => {
    expect(decide("/", "", PUB, false)).toEqual({});
    expect(decide("/roster", "", PUB, false)).toEqual({});
    expect(decide("/roster", `?league=${PUB}`, null, false)).toEqual({});
  });
  it("a global page (NFL, News, Settings) leaves the last league alone", () => {
    for (const p of ["/nfl", "/nfl/8/DET", "/news", "/news/player/1", "/settings", "/plays"]) expect(decide(p, "", PUB, true)).toEqual({});
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
