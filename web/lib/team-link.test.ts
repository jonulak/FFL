import { describe, expect, it } from "vitest";
import { parseTeamParam, teamHref } from "./team-link";

describe("teamHref", () => {
  it("links an id from the API or a CSV", () => {
    expect(teamHref(7)).toBe("/team/7");
    expect(teamHref("6")).toBe("/team/6");
  });
  it("gives a missing or malformed id no link", () => {
    expect(teamHref(undefined)).toBeNull();
    expect(teamHref(null)).toBeNull();
    expect(teamHref("")).toBeNull();
    expect(teamHref("henry")).toBeNull();
    expect(teamHref(0)).toBeNull();
  });
});

describe("parseTeamParam", () => {
  it("reads a positive integer", () => {
    expect(parseTeamParam("3")).toBe(3);
  });
  it("refuses anything else", () => {
    for (const s of ["", "0", "-1", "2.5", "x"]) expect(parseTeamParam(s)).toBeNull();
  });
});
