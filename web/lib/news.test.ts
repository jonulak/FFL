import { describe, expect, it } from "vitest";
import type { NewsRow } from "./api";
import { arrow, filterHref, ownerTag, parseId, parseNewsFilter, playerNewsHref, splitRows, statusLabel } from "./news";

const row = (over: Partial<NewsRow> = {}): NewsRow => ({
  player_id: 1, name: "Blake Corum", pos: "RB", team: "LAR", effect: "up", kind: "role", reason: null, flag: false,
  owner: null, mine: false, ...over,
});

describe("arrow", () => {
  it("draws the effect", () => {
    expect(arrow("up")).toBe("▲");
    expect(arrow("down")).toBe("▼");
    expect(arrow("none")).toBe("●");
  });
});

describe("filterHref", () => {
  it("puts the filter in the URL, All as the bare page", () => {
    expect(filterHref("fa")).toBe("/news?filter=fa");
    expect(filterHref("mine")).toBe("/news?filter=mine");
    expect(filterHref("all")).toBe("/news");
  });
});

describe("parseNewsFilter", () => {
  it("takes the four filters and falls back to All", () => {
    expect(parseNewsFilter("others")).toBe("others");
    expect(parseNewsFilter("junk")).toBe("all");
    expect(parseNewsFilter(undefined)).toBe("all");
  });
});

describe("ownerTag", () => {
  it("says Mine, FA or the holding team's abbreviation", () => {
    expect(ownerTag(row({ mine: true, owner: { team_id: 7, name: "My Team", abbrev: "JST" } }))).toBe("Mine");
    expect(ownerTag(row())).toBe("FA");
    expect(ownerTag(row({ owner: { team_id: 3, name: "My Njigbas", abbrev: "NJIG" } }))).toBe("NJIG");
  });
  it("falls back to the team name without an abbreviation", () => {
    expect(ownerTag(row({ owner: { team_id: 3, name: "My Njigbas", abbrev: null } }))).toBe("My Njigbas");
  });
});

describe("splitRows", () => {
  it("parts the arrows from the no-change rows, order kept", () => {
    const rows = [row({ player_id: 1 }), row({ player_id: 2, effect: "none" }), row({ player_id: 3, effect: "down" })];
    const { moved, none } = splitRows(rows);
    expect(moved.map((r) => r.player_id)).toEqual([1, 3]);
    expect(none.map((r) => r.player_id)).toEqual([2]);
  });
});

describe("statusLabel", () => {
  it("names a corrected or retracted story, nothing for a live one", () => {
    expect(statusLabel("retracted")).toBe("Retracted");
    expect(statusLabel("corrected")).toBe("Corrected");
    expect(statusLabel("live")).toBeNull();
  });
});

describe("parseId", () => {
  it("takes a positive integer, nothing else", () => {
    expect(parseId("42")).toBe(42);
    expect(parseId("x")).toBeNull();
    expect(parseId("-3")).toBeNull();
    expect(parseId(undefined)).toBeNull();
    expect(parseId(["1", "2"])).toBeNull();
  });
});

describe("playerNewsHref", () => {
  it("is the player's news page", () => {
    expect(playerNewsHref(4430737)).toBe("/news/player/4430737");
  });
});
