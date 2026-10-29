import { describe, expect, it } from "vitest";
import { arrivedAccount, byAccount, canPaste, espnMessage, initials, loginLabel, newRefusal, platformOf, pickable,
         resentAccount,
         type EspnAccount, type EspnStatus, type LeagueChoice } from "./espn";

const acct = (id: number, updated_at: string, connected = true): EspnAccount => ({
  id, connected, updated_at, last_ok: null, failing_since: connected ? null : "2026-10-27", failing_reason: null,
  unreadable: false, leagues: [],
});
const status = (accounts: EspnAccount[]): EspnStatus => ({ connected: true, failing_since: null, accounts, devices: 0 });
const league = (over: Partial<LeagueChoice>): LeagueChoice => ({
  league_id: "1", key: "espn:1", season: 2026, name: "L", size: 10, team_id: 1, team: "T", logo: null, account: 1,
  ok: true, problem: null, joined: false, ...over,
});

describe("platformOf", () => {
  it.each([
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 Version/26.6 Mobile/15E148 Safari/604.1", "ios"],
    ["Mozilla/5.0 (iPad; CPU OS 18_7 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148", "ios"],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/26.5 Safari/605.1.15", "mac-safari"],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/156.0.0.0 Safari/537.36", "chrome"],
    ["Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/156.0 Safari/537.36 Edg/156.0", "chrome"],
    ["Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/156.0 Mobile Safari/537.36", "other"],
    ["", "other"],
  ])("%s", (ua, want) => expect(platformOf(ua)).toBe(want));

  it("offers the paste on a computer only", () => {
    expect(canPaste("ios")).toBe(false);
    expect(canPaste("chrome")).toBe(true);
  });
});

describe("arrivedAccount", () => {
  it("on a first run takes any working login, the newest", () => {
    const s = status([acct(1, "2026-10-28T10:00"), acct(2, "2026-10-29T10:00"), acct(3, "2026-10-30T10:00", false)]);
    expect(arrivedAccount(s, [1, 2, 3], true)?.id).toBe(2);
  });
  it("adding a login waits for one that wasn't there", () => {
    const s = status([acct(1, "2026-10-29T10:00")]);
    expect(arrivedAccount(s, [1], false)).toBeNull();
    expect(arrivedAccount(status([...s.accounts, acct(4, "2026-10-28T09:00")]), [1], false)?.id).toBe(4);
  });
  it("a login already connected, sent again, is told apart from a new one", () => {
    const before = [{ id: 1, updated_at: "2026-10-29T10:00" }];
    expect(resentAccount(status([acct(1, "2026-10-29T10:00")]), before)).toBeNull();
    expect(resentAccount(status([acct(1, "2026-10-29T10:05")]), before)?.id).toBe(1);
  });
  it("a refused login never counts as arrived", () => {
    expect(arrivedAccount(status([acct(5, "2026-10-29T10:00", false)]), [], true)).toBeNull();
  });
});

describe("Settings' logins", () => {
  it("names each by its place and groups its leagues by name", () => {
    const accounts = [acct(7, "a"), acct(3, "b")];
    expect(loginLabel(accounts, 3)).toBe("ESPN login 2");
    const groups = byAccount(accounts, [league({ key: "b", name: "Beta", account: 7 }),
                                        league({ key: "a", name: "Alpha", account: 7 }),
                                        league({ key: "c", name: "Gamma", account: 3 })]);
    expect(groups.map((g) => [g.account.id, g.leagues.map((l) => l.key)])).toEqual([[7, ["a", "b"]], [3, ["c"]]]);
  });
});

describe("newRefusal", () => {
  it("is a refused send that came after the page opened, never the one it opened with", () => {
    const r = { error: "unknown_login", message: "out of date", at: 100 };
    expect(newRefusal({ ...status([]), refused: r }, status([]))).toEqual(r);
    expect(newRefusal({ ...status([]), refused: r }, { ...status([]), refused: r })).toBeNull();
    expect(newRefusal({ ...status([]), refused: { ...r, at: 200 } }, { ...status([]), refused: r })?.at).toBe(200);
    expect(newRefusal({ ...status([]), refused: null }, status([]))).toBeNull();
  });
});

describe("the rest", () => {
  it("a league is pickable when ESPN allows it and it isn't in FFL yet", () => {
    expect(pickable(league({}))).toBe(true);
    expect(pickable(league({ joined: true }))).toBe(false);
    expect(pickable(league({ ok: false }))).toBe(false);
  });
  it("reads the API's own sentence", () => {
    expect(espnMessage({ detail: { error: "refused", message: "ESPN refused this login." } })).toBe("ESPN refused this login.");
    expect(espnMessage({ detail: [{ msg: "x" }] }, "fallback")).toBe("fallback");
    expect(espnMessage(null, "fallback")).toBe("fallback");
  });
  it("initials", () => {
    expect(initials("My Team")).toBe("JST");
    expect(initials(null)).toBe("?");
  });
});
