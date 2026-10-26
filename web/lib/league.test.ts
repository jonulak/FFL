import { describe, expect, it } from "vitest";
import { decide, leagueHref, leagueOf, START_URL, switchPath, tabsFor, withLeague } from "./league";

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
  it("an empty league (a call naming home) stays on home and takes another league's place off home", () => {
    expect(withLeague("/outlook/refresh?league=", null)).toBe("/outlook/refresh?league=");
    expect(withLeague("/outlook/refresh?league=", PUB)).toBe(`/outlook/refresh?league=${encodeURIComponent(PUB)}`);
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
  it("the Matchup page with a league is that league's page; plain it is the start page", () => {
    expect(decide("/", `?league=${PUB}`, null, true)).toEqual({ cookie: PUB });
    expect(decide("/", `?league=${PUB}`, PUB, true)).toEqual({});
    expect(decide("/", "?week=3", PUB, true)).toEqual({ cookie: null });
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
  it("every league opens every tab; another league's each carry it (the global ones too, so the way back stays in it)", () => {
    expect(tabsFor(tabs, null)).toEqual(tabs.map(([h, l]) => [h, l, h]));
    expect(tabsFor(tabs, PUB)).toEqual(tabs.map(([h, l]) => [h, l, withLeague(h, PUB)]));
  });
});

describe("switchPath (the page a league switch opens)", () => {
  it("a league's own page opens the same page in the other league, its query left behind", () => {
    expect(switchPath("/roster", "")).toBe("/roster");
    expect(switchPath("/", "?week=3")).toBe("/");
    expect(switchPath("/players", "?preset=abc&pos=WR&league=espn%3A1")).toBe("/players");
    expect(switchPath("/transactions", "")).toBe("/transactions");
    expect(switchPath("/settings/league", "?league=espn%3A1")).toBe("/settings/league");
  });
  it("a page of one team or one week falls back to its list: the other league's ids and weeks differ", () => {
    expect(switchPath("/team/3", "")).toBe("/league");
    expect(switchPath("/league/8/4", "")).toBe("/league");
    expect(switchPath("/league/8", "")).toBe("/league");
    expect(switchPath("/decisions", "?week=6")).toBe("/decisions");
  });
  it("a page every league shares stays where it is, query and all (less the league)", () => {
    expect(switchPath("/nfl/8/DET", "?f=mine&league=espn%3A1")).toBe("/nfl/8/DET?f=mine");
    expect(switchPath("/news", "?filter=mine")).toBe("/news?filter=mine");
    expect(switchPath("/settings", "")).toBe("/settings");
  });
});

describe("leagueHref (every in-app link and router move)", () => {
  it("carries the open league onto an in-app page", () => {
    expect(leagueHref("/team/3", PUB)).toBe(withLeague("/team/3", PUB));
    expect(leagueHref("/league/8/4?x=1", PUB)).toBe(withLeague("/league/8/4?x=1", PUB));
  });
  it("home leaves every link as it was", () => {
    expect(leagueHref("/team/3", null)).toBe("/team/3");
  });
  it("never rewrites a link that names its own league, an API path, an outside or a relative link", () => {
    expect(leagueHref("/roster?league=espn%3A1", PUB)).toBe("/roster?league=espn%3A1");
    expect(leagueHref("/api/logo/3", PUB)).toBe("/api/logo/3");
    expect(leagueHref("https://espn.com/x", PUB)).toBe("https://espn.com/x");
    expect(leagueHref("#top", PUB)).toBe("#top");
  });
});
