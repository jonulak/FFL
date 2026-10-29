import { describe, expect, it } from "vitest";
import { ago, ChangeWatch, distinctLogins, fromCookieString, pairHash, pickStore, sendOutcome, signedIn, swidKey, viewOf } from "./pure";

const SWID = "{AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE}";
const OTHER = "{11111111-2222-3333-4444-555555555555}";
const S2 = "AEB" + "x".repeat(150) + "%2B" + "y".repeat(100) + "%3D";

describe("fromCookieString", () => {
  it("reads both cookies raw, never decoded", () => {
    const got = fromCookieString(`region=unknown; espn_s2=${S2}; SWID=${SWID}; _ga=GA1`);
    expect(got).toEqual({ espn_s2: S2, swid: SWID });
  });
  it("lists every SWID when the browser holds several", () => {
    expect(fromCookieString(`SWID=${OTHER}; espn_s2=${S2}; SWID=${SWID}`)).toEqual({ espn_s2: S2, swid: SWID, swids: [OTHER, SWID] });
  });
  it("is empty without them", () => {
    expect(fromCookieString("region=unknown; _ga=GA1")).toEqual({});
    expect(fromCookieString("")).toEqual({});
  });
});

describe("signedIn and swidKey", () => {
  it("a braced GUID with espn_s2 is a login; an anonymous SWID is not", () => {
    expect(signedIn({ espn_s2: S2, swid: SWID })).toBe(true);
    expect(signedIn({ espn_s2: S2, swid: "%7BAAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE%7D" })).toBe(true);
    expect(signedIn({ espn_s2: S2, swid: "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE" })).toBe(false);
    expect(signedIn({ swid: SWID })).toBe(false);
  });
  it("compares a SWID however it was copied", () => {
    expect(swidKey("%7Baaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee%7D")).toBe(swidKey(SWID));
  });
});

describe("pickStore", () => {
  const a = { storeId: "1", espn_s2: S2, swid: SWID };
  const b = { storeId: "2", espn_s2: S2 + "b", swid: OTHER };
  it("takes the store holding the page's login", () => {
    expect(pickStore([a, b], { swid: OTHER })).toEqual({ espn_s2: S2 + "b", swid: OTHER });
    expect(pickStore([{ storeId: "0" }, a], { swid: "{" + swidKey(SWID).toLowerCase() + "}" })).toEqual({ espn_s2: S2, swid: SWID });
  });
  it("with no page to tell them apart, the only signed-in store, else none", () => {
    expect(pickStore([{ storeId: "persistent-1" }, a], null)).toEqual({ espn_s2: S2, swid: SWID });
    expect(pickStore([a, b], null)).toBeNull();
    expect(pickStore([], null)).toBeNull();
  });
  it("hands on every other SWID the browser holds, so a stale one beside the login's is never the only try", () => {
    const stale = { storeId: "1", espn_s2: S2, swid: SWID, swids: [SWID, OTHER, "anon-unbraced"] };
    expect(pickStore([stale], null)).toEqual({ espn_s2: S2, swid: SWID, others: [OTHER] });
    // the page sees the other one: still that store, both tried
    expect(pickStore([stale], { swid: OTHER })).toEqual({ espn_s2: S2, swid: SWID, others: [OTHER] });
    // a SWID only the page holds rides along too
    const third = "{99999999-8888-7777-6666-555555555555}";
    expect(pickStore([a], { swid: SWID, swids: [SWID, third] })).toEqual({ espn_s2: S2, swid: SWID, others: [third] });
  });
});

describe("distinctLogins", () => {
  it("keeps braced SWIDs once each, however copied, in order", () => {
    expect(distinctLogins([SWID, undefined, "anon", "%7B" + SWID.slice(1, -1).toLowerCase() + "%7D", OTHER])).toEqual([SWID, OTHER]);
  });
});

describe("pairHash", () => {
  it("is the same for one login however its SWID was copied, and differs when espn_s2 does", async () => {
    const h = await pairHash({ espn_s2: S2, swid: SWID });
    expect(h).toMatch(/^[0-9a-f]{64}$/);
    expect(await pairHash({ espn_s2: S2, swid: "%7B" + SWID.slice(1, -1).toLowerCase() + "%7D" })).toBe(h);
    expect(await pairHash({ espn_s2: S2 + "z", swid: SWID })).not.toBe(h);
    expect(h).not.toContain("AAAAAAAA");
  });
});

describe("ChangeWatch", () => {
  it("reports a login the first time, then only a different one", () => {
    const w = new ChangeWatch();
    expect(w.seen({})).toBe(false);                              // signed out: nothing
    expect(w.seen({ espn_s2: S2, swid: SWID })).toBe(true);
    expect(w.seen({ espn_s2: S2, swid: SWID })).toBe(false);     // the same, every 3 s
    expect(w.seen({ espn_s2: S2 + "z", swid: SWID })).toBe(true); // ESPN renewed it
    expect(w.seen({ espn_s2: S2, swid: OTHER })).toBe(true);      // signed in as someone else
    expect(w.seen({})).toBe(false);                              // signed out: nothing to send
  });
});

describe("sendOutcome", () => {
  it("remembers a sent login and one refused for good; retries the rest; a 401 unpairs", () => {
    expect(sendOutcome(200, null)).toMatchObject({ ok: true, remember: true, unpaired: false });
    expect(sendOutcome(422, "ESPN refused this login.")).toEqual(
      { ok: false, remember: true, unpaired: false, message: "ESPN refused this login." });
    expect(sendOutcome(429, null)).toMatchObject({ remember: false });
    expect(sendOutcome(502, null)).toMatchObject({ remember: false });
    expect(sendOutcome(0, null)).toMatchObject({ remember: false });
    expect(sendOutcome(401, null)).toMatchObject({ unpaired: true, remember: false });
  });
});

describe("viewOf", () => {
  const now = 1_000_000;
  it("shows the code while a pairing lives, else paired or the disclosure", () => {
    expect(viewOf({}, now)).toEqual({ kind: "disclose" });
    expect(viewOf({ pairing: { code: "ABCD-EFGH", poll_secret: "s", expires_at: now + 9.5 * 60_000 } }, now))
      .toEqual({ kind: "code", code: "ABCD-EFGH", minutesLeft: 10 });
    expect(viewOf({ pairing: { code: "ABCD-EFGH", poll_secret: "s", expires_at: now - 1 } }, now)).toEqual({ kind: "disclose" });
    const last = { at: now, ok: true, message: "m" };
    expect(viewOf({ token: "t", last }, now)).toEqual({ kind: "paired", last, pairing: false });
    // pairing again while paired shows the new code
    expect(viewOf({ token: "t", pairing: { code: "WXYZ-2345", poll_secret: "s", expires_at: now + 60_000 } }, now).kind).toBe("code");
  });
  it("ago", () => {
    expect(ago(now, now + 30_000)).toBe("just now");
    expect(ago(now, now + 4 * 60_000)).toBe("4 min ago");
    expect(ago(now, now + 3 * 3_600_000)).toBe("3 h ago");
    expect(ago(now, now + 49 * 3_600_000)).toBe("2 days ago");
  });
});
