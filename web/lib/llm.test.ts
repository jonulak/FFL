import { describe, expect, it } from "vitest";
import { tokenError, wizardStart } from "./llm";

const NONE = { app_url: "https://ffl.example", push_contact: null, youtube_key_set: false };

describe("wizardStart", () => {
  it("opens on Claude for an admin with no token", () => {
    expect(wizardStart({ set: false, source: null }, NONE, false)).toBe("claude");
    expect(wizardStart({ set: false, source: null }, NONE, true)).toBe("claude");
  });
  it("then Services while none of them is set", () => {
    expect(wizardStart({ set: true, source: "db" }, NONE, false)).toBe("services");
    expect(wizardStart({ set: true, source: "db" }, { ...NONE, youtube_key_set: true }, false)).toBe("connect");
    expect(wizardStart({ set: true, source: "db" }, { ...NONE, push_contact: "me@example.com" }, true)).toBe("leagues");
  });
  it("skips both for a non-admin (no statuses)", () => {
    expect(wizardStart(null, null, false)).toBe("connect");
    expect(wizardStart(null, null, true)).toBe("leagues");
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
