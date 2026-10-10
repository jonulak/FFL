import { describe, expect, it } from "vitest";
import { isHm, noticeState, pushSupport, urlBase64ToUint8Array } from "./notify";

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

describe("noticeState", () => {
  const n = { key: "k", kind: "move", title: "t", body: "b", url: "/", created_at: "x", sent_at: null, outcome: null };
  it("names the state", () => {
    expect(noticeState(n)).toBe("held");
    expect(noticeState({ ...n, sent_at: "y", outcome: "sent" })).toBe("sent");
    expect(noticeState({ ...n, sent_at: "y", outcome: "off" })).toBe("switched off");
    expect(noticeState({ ...n, sent_at: "y", outcome: "expired" })).toBe("too old");
  });
});
