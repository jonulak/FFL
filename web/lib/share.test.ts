import { describe, expect, it } from "vitest";
import { markSource, mixHex, parseShare, shareFile, shareHref } from "./share";

const q = (s: string) => new URLSearchParams(s);

describe("parseShare", () => {
  it("reads a roster and a league game", () => {
    expect(parseShare(q("kind=roster&week=9"))).toEqual({ kind: "roster", week: 9, team: null, teams: true });
    expect(parseShare(q("kind=matchup&week=9&team=4"))).toEqual({ kind: "matchup", week: 9, team: 4, teams: true });
  });
  it("leaves the teams out only on teams=0", () => {
    expect(parseShare(q("kind=roster&week=9&teams=0"))?.teams).toBe(false);
  });
  it("refuses anything that is not a kind and positive integers", () => {
    for (const s of ["", "kind=other&week=9", "kind=roster", "kind=roster&week=0", "kind=roster&week=1.5", "kind=roster&week=9/../x", "kind=matchup&week=9&team=", "kind=matchup&week=9&team=x", "kind=roster&week=9&teams=1", "kind=roster&week=9&teams="]) {
      expect(parseShare(q(s))).toBeNull();
    }
  });
});

describe("markSource", () => {
  it("takes the API's own marks and ESPN's CDN", () => {
    expect(markSource("/api/logo/7")).toEqual({ api: "/logo/7" });
    expect(markSource("https://g.espncdn.com/lm-static/logo.png")).toEqual({ cdn: "https://g.espncdn.com/lm-static/logo.png" });
  });
  it("refuses any other address an owner could set", () => {
    for (const s of ["", "not a url", "/api/../secret", "http://g.espncdn.com/a.png", "https://169.254.169.254/a.png", "https://evil.example/a.png", "https://espncdn.com.evil.example/a.png", "https://evilespncdn.com/a.png"]) {
      expect(markSource(s)).toBeNull();
    }
  });
});

describe("mixHex", () => {
  it("mixes by share", () => {
    expect(mixHex("#ffffff", "#000000", 0.5)).toBe("#808080");
    expect(mixHex("#97233F", "#000000", 1)).toBe("#97233f");
  });
});

describe("shareHref", () => {
  it("is what parseShare reads back", () => {
    for (const want of [{ kind: "roster", week: 9, team: null, teams: true }, { kind: "matchup", week: 3, team: 4, teams: false }] as const) {
      expect(parseShare(new URLSearchParams(shareHref(want).split("?")[1]))).toEqual(want);
    }
  });
});

describe("shareFile", () => {
  const file = new File(["x"], "x.png", { type: "image/png" });
  const failing = (name: string) => ({ canShare: () => true, share: () => Promise.reject(Object.assign(new Error(name), { name })) });
  it("hands the file to the sheet", async () => {
    let got: ShareData | undefined;
    const nav = { canShare: () => true, share: async (d: ShareData) => { got = d; } };
    expect(await shareFile(file, nav)).toBe("shared");
    expect(got?.files).toEqual([file]);
  });
  it("tells a cancel from a stale tap", async () => {
    expect(await shareFile(file, failing("AbortError"))).toBe("cancelled");
    expect(await shareFile(file, failing("NotAllowedError"))).toBe("blocked");
  });
  it("says when a browser cannot share a file", async () => {
    expect(await shareFile(file, {})).toBe("unsupported");
    expect(await shareFile(file, { canShare: () => false, share: async () => {} })).toBe("unsupported");
  });
  it("lets any other failure through", async () => {
    await expect(shareFile(file, failing("DataError"))).rejects.toThrow("DataError");
  });
});
