import { describe, expect, it } from "vitest";
import type { NewsRow, PlayerNews, Story } from "./api";
import { arrow, filterHref, ownerTag, parseId, parseNewsFilter, playerNewsHref, recentFor, splitRows, statusLabel } from "./news";

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
  it("carries the no-change switch and a story target", () => {
    expect(filterHref("all", true)).toBe("/news?quiet=1");
    expect(filterHref("fa", true)).toBe("/news?filter=fa&quiet=1");
    expect(filterHref("all", false, 7)).toBe("/news?story=7");
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

  it("keeps my no-change rows out under My players", () => {
    const rows = [row({ player_id: 1 }), row({ player_id: 2, effect: "none", mine: true }), row({ player_id: 3, effect: "none" })];
    expect(splitRows(rows).none.map((r) => r.player_id)).toEqual([2, 3]);
    const { moved, none } = splitRows(rows, true);
    expect(moved.map((r) => r.player_id)).toEqual([1, 2]);
    expect(none.map((r) => r.player_id)).toEqual([3]);
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

describe("recentFor", () => {
  const story = (id: number, players: NewsRow[]): Story => ({
    id, headline: `h${id}`, overview: "", status: "live", status_note: null, updated: false, at: "2026-10-18T12:00:00+00:00",
    first: null, sources: [], players,
  });
  const page = (stories: Story[]): PlayerNews => ({
    player: { player_id: 1, name: "Blake Corum", pos: "RB", team: "LAR", owner: null, mine: false }, muted: false,
    stories, next_before: null,
  });

  it("keeps the newest three stories, each with his row only", () => {
    const d = page([1, 2, 3, 4].map((i) => story(i, [row({ player_id: 9 }), row({ player_id: 1, effect: "down" })])));
    const out = recentFor(d);
    expect(out.map((x) => x.story.id)).toEqual([1, 2, 3]);
    expect(out.every((x) => x.row.player_id === 1 && x.row.effect === "down")).toBe(true);
  });
  it("skips a story without his row, and a missing read is no news", () => {
    expect(recentFor(page([story(1, [row({ player_id: 9 })]), story(2, [row({ player_id: 1 })])])).map((x) => x.story.id)).toEqual([2]);
    expect(recentFor(null)).toEqual([]);
  });
});
