import { describe, expect, it } from "vitest";
import { isHm, movesStep, openRequest, pushSupport, urlBase64ToUint8Array } from "./notify";

describe("movesStep", () => {
  it("takes the first count it sees as the baseline", () => {
    expect(movesStep(null, "17 m3")).toEqual({ moves: 3, refresh: false });
  });
  it("refreshes when the count moves: a move tick, or a reconnect after one was missed", () => {
    expect(movesStep(3, "m4")).toEqual({ moves: 4, refresh: true });
    expect(movesStep(3, "20 m4")).toEqual({ moves: 4, refresh: true });
  });
  it("ignores points ticks and a reconnect with nothing missed", () => {
    expect(movesStep(3, "18")).toEqual({ moves: 3, refresh: false });
    expect(movesStep(3, "18 m3")).toEqual({ moves: 3, refresh: false });
    expect(movesStep(null, "18")).toEqual({ moves: null, refresh: false });
    expect(movesStep(3, undefined)).toEqual({ moves: 3, refresh: false });
  });
});

describe("openRequest", () => {
  const origin = "https://macbook-pro.tailnet.ts.net:3300";
  it("takes the worker's open message to an in-app path", () => {
    expect(openRequest({ type: "open", url: `${origin}/transactions` }, origin)).toBe("/transactions");
    expect(openRequest({ type: "open", url: `${origin}/team/3?week=5#x` }, origin)).toBe("/team/3?week=5#x");
  });
  it("ignores other messages, other origins and junk", () => {
    expect(openRequest({ type: "hello" }, origin)).toBeNull();
    expect(openRequest({ type: "open", url: "https://evil.example/transactions" }, origin)).toBeNull();
    expect(openRequest({ type: "open", url: 42 }, origin)).toBeNull();
    expect(openRequest(null, origin)).toBeNull();
  });
});

describe("urlBase64ToUint8Array", () => {
  it("decodes base64url without padding", () => {
    expect(Array.from(urlBase64ToUint8Array("AQID_-8"))).toEqual([1, 2, 3, 255, 239]);
  });
});

describe("pushSupport", () => {
  const base = { hasServiceWorker: true, hasPushManager: true, standalone: true, ios: true };
  it("is ready in the Home Screen app", () => expect(pushSupport(base)).toBe("ready"));
  it("asks for the Home Screen on iOS Safari", () =>
    expect(pushSupport({ ...base, standalone: false, hasPushManager: false })).toBe("needs-install"));
  it("is ready in a desktop browser tab", () => expect(pushSupport({ ...base, ios: false, standalone: false })).toBe("ready"));
  it("is unsupported without a service worker", () => expect(pushSupport({ ...base, hasServiceWorker: false })).toBe("unsupported"));
});

describe("isHm", () => {
  it("takes 24h HH:MM only", () => {
    expect(isHm("07:00")).toBe(true);
    expect(isHm("23:59")).toBe(true);
    expect(isHm("24:00")).toBe(false);
    expect(isHm("7:00")).toBe(false);
  });
});

