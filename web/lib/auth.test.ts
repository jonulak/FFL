import { describe, expect, it } from "vitest";
import { checksSetup, errorCode, isSetupPath, needsSignIn, safeNext, SESSION_COOKIE, sessionCookie, signedOutRedirect, signInHref } from "./auth";

const PUB = "espn:950458410";

describe("SESSION_COOKIE", () => {
  it("is the API's cookie name", () => expect(SESSION_COOKIE).toBe("ffl_session"));
});

describe("sessionCookie", () => {
  it("is the default name unless FFL_SESSION_COOKIE names another, as the API reads it", () => {
    expect(sessionCookie({})).toBe("ffl_session");
    expect(sessionCookie({ FFL_SESSION_COOKIE: "" })).toBe("ffl_session");
    expect(sessionCookie({ FFL_SESSION_COOKIE: "ffl_session_3302" })).toBe("ffl_session_3302");
  });
});

describe("needsSignIn", () => {
  it("sends a page with no session to sign in", () => {
    expect(needsSignIn("/roster", false)).toBe(true);
    expect(needsSignIn("/", false)).toBe(true);
  });
  it("lets a page with a session through (the API checks it)", () => {
    expect(needsSignIn("/roster", true)).toBe(false);
  });
  it("lets the privacy policy through signed out", () => {
    expect(needsSignIn("/privacy", false)).toBe(false);
  });
  it("never sends /signin to itself", () => {
    expect(needsSignIn("/signin", false)).toBe(false);
    expect(needsSignIn("/signin", true)).toBe(false);
  });
  it("is not fooled by a page that only starts with signin", () => {
    expect(needsSignIn("/signinx", false)).toBe(true);
  });
  it("lets first-run setup and a setup link through signed out", () => {
    expect(needsSignIn("/setup", false)).toBe(false);
    expect(needsSignIn("/setup/abc", false)).toBe(false);
    expect(needsSignIn("/setupx", false)).toBe(true);
  });
});

describe("checksSetup", () => {
  it("checks on /signin even with a (stale) session cookie", () => {
    expect(checksSetup("/signin", true)).toBe(true);
    expect(checksSetup("/signin", false)).toBe(true);
  });
  it("checks a signed-out page, never a signed-in one, setup or the privacy policy", () => {
    expect(checksSetup("/roster", false)).toBe(true);
    expect(checksSetup("/roster", true)).toBe(false);
    expect(checksSetup("/setup", false)).toBe(false);
    expect(checksSetup("/setup/abc", true)).toBe(false);
    expect(checksSetup("/privacy", false)).toBe(false);
  });
});

describe("isSetupPath", () => {
  it("is /setup and the pages under it only", () => {
    expect(isSetupPath("/setup")).toBe(true);
    expect(isSetupPath("/setup/tok")).toBe(true);
    expect(isSetupPath("/setupx")).toBe(false);
    expect(isSetupPath("/signin")).toBe(false);
  });
});

describe("errorCode", () => {
  it("reads the API's code at the top level or under detail", () => {
    expect(errorCode({ error: "bad_login" })).toBe("bad_login");
    expect(errorCode({ detail: { error: "not_admin", message: "x" } })).toBe("not_admin");
  });
  it("is null for anything else", () => {
    for (const b of [null, undefined, "x", {}, { detail: "Not Found" }, { error: 3 }, { detail: [{ msg: "x" }] }]) {
      expect(errorCode(b)).toBeNull();
    }
  });
});

describe("signInHref", () => {
  it("carries the page in next", () => {
    expect(signInHref("/roster", "")).toBe("/signin?next=%2Froster");
  });
  it("keeps the query, so ?league= survives the round trip", () => {
    const href = signInHref("/players", `?pos=WR&league=${encodeURIComponent(PUB)}`);
    const next = new URL(href, "http://x").searchParams.get("next")!;
    expect(next).toBe(`/players?pos=WR&league=${encodeURIComponent(PUB)}`);
    expect(new URL(next, "http://x").searchParams.get("league")).toBe(PUB);
  });
  it("takes a search with or without its ?", () => {
    expect(signInHref("/roster", "launch=1")).toBe(signInHref("/roster", "?launch=1"));
  });
  it("names no next for the start page", () => {
    expect(signInHref("/", "")).toBe("/signin");
  });
  it("never points next at /signin itself", () => {
    expect(signInHref("/signin", "?error=failed")).toBe("/signin");
  });
});

describe("safeNext", () => {
  it("keeps a path on this site", () => {
    expect(safeNext("/roster?league=a")).toBe("/roster?league=a");
  });
  it("turns anything else into /", () => {
    for (const bad of [null, "", "roster", "//evil.com", "https://evil.com", "/\\evil.com", "/\tevil", "/a\\b"]) {
      expect(safeNext(bad)).toBe("/");
    }
  });
});

describe("signedOutRedirect", () => {
  it("sends a signed-out 401 to sign in, back to the page", () => {
    expect(signedOutRedirect(401, { error: "signed_out" }, "/roster", "?league=x")).toBe(signInHref("/roster", "?league=x"));
  });
  it("defaults to the start page", () => {
    expect(signedOutRedirect(401, { error: "signed_out" })).toBe("/signin");
  });
  it("leaves every other answer to the caller", () => {
    expect(signedOutRedirect(403, { error: "signed_out" })).toBeNull();
    expect(signedOutRedirect(500, { error: "signed_out" })).toBeNull();
    expect(signedOutRedirect(401, { detail: "nope" })).toBeNull();
    expect(signedOutRedirect(401, null)).toBeNull();
    expect(signedOutRedirect(200, { error: "signed_out" })).toBeNull();
  });
});
