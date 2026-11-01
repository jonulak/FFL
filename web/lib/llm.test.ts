import { describe, expect, it } from "vitest";
import { tokenError, wizardStart } from "./llm";

describe("wizardStart", () => {
  it("opens on Claude for an admin with no token", () => {
    expect(wizardStart({ set: false, source: null }, false)).toBe("claude");
    expect(wizardStart({ set: false, source: null }, true)).toBe("claude");
  });
  it("skips Claude when a token is set, or for a non-admin (no status)", () => {
    expect(wizardStart({ set: true, source: "env" }, false)).toBe("connect");
    expect(wizardStart({ set: true, source: "db" }, true)).toBe("leagues");
    expect(wizardStart(null, false)).toBe("connect");
    expect(wizardStart(null, true)).toBe("leagues");
  });
});

describe("tokenError", () => {
  it("says the ping's reason", () => {
    expect(tokenError({ error: "claude_failed", message: "Invalid API key" }, "502 Bad Gateway"))
      .toBe("Claude didn't accept that token: Invalid API key");
    expect(tokenError({ error: "claude_failed" }, "502 Bad Gateway")).toBe("Claude didn't accept that token.");
  });
  it("names a blank paste", () => expect(tokenError({ error: "no_token" }, "422")).toBe("Paste a token first."));
  it("falls back to the status line", () => expect(tokenError(null, "500 Internal Server Error")).toBe("Not saved: 500 Internal Server Error"));
});
