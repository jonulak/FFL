import { describe, expect, it } from "vitest";
import { versionLine } from "./version";

const A = "a".repeat(40), B = "b".repeat(40);

describe("versionLine", () => {
  it("says up to date with the short commit", () => {
    expect(versionLine({ commit: A, latest: A, behind: false, pinned: null })).toBe("Up to date: aaaaaaa.");
  });

  it("names both commits when behind", () => {
    expect(versionLine({ commit: A, latest: B, behind: true, pinned: null }))
      .toBe("A new version is out. This install runs aaaaaaa; the latest is bbbbbbb.");
  });

  it("does not claim up to date when GitHub could not say", () => {
    expect(versionLine({ commit: A, latest: null, behind: false, pinned: null }))
      .toBe("Running aaaaaaa. GitHub couldn't be reached, so there's no word on a newer version.");
  });

  it("names the pin", () => {
    expect(versionLine({ commit: A, latest: null, behind: false, pinned: "v1.2.0" }))
      .toBe("Pinned to v1.2.0 by FFL_VERSION in .env.");
  });

  it("reads a build with no commit as development", () => {
    expect(versionLine({ commit: null, latest: B, behind: false, pinned: null }))
      .toBe("A development build, with no release to compare it to.");
  });
});
