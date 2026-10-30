import { describe, expect, it } from "vitest";
import { needsSignIn, safeNext, SESSION_COOKIE, sessionCookie, signedOutRedirect, signInHref } from "./auth";

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
