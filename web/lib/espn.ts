// Connecting ESPN (ffl-soc.6.3, ffl/espn_connect.py): the wizard on /welcome and Settings › ESPN connection share these
// shapes and decisions. Pure: tested in espn.test.ts.

// Why a listed league can't be picked: `message` is the hover's line, `fix` the dialog's (PROBLEMS in espn_connect).
export type LeagueProblem = { error: string; title: string; message: string; fix: string };

export type LeagueChoice = {
  league_id: string; key: string; season: number; name: string; size: number | null;
  team_id: number; team: string | null; logo: string | null;
  account: number;               // the ESPN login (espn_credentials row) that lists it
  ok: boolean; problem: LeagueProblem | null; joined: boolean;
};
export type LeagueChoices = { season: number; leagues: LeagueChoice[]; problems: { account: number; error: string; message: string }[] };

export type EspnAccount = {
  id: number; connected: boolean; updated_at: string; last_ok: string | null;
  failing_since: string | null; failing_reason: string | null; unreadable: boolean;
  leagues: string[];             // the keys of the leagues it serves here
};
// `refused`: the latest extension send ESPN turned down (15 minutes; none once a login connects), so a page waiting for
// the login can say why none came.
export type Refusal = { error: string; message: string; at: number };
export type EspnStatus = {
  connected: boolean; failing_since: string | null; accounts: EspnAccount[]; devices: number; refused?: Refusal | null;
};

// A refusal that came after the page opened (`before`: the status it opened with): the waiting line's reason.
export function newRefusal(st: EspnStatus, before: EspnStatus): Refusal | null {
  const r = st.refused;
  return r && r.at !== before.refused?.at ? r : null;
}
export type ExtensionDevice = { id: number; label: string; created_at: string; last_used_at: string | null };

// One league's add-or-join answer (POST /espn/leagues).
export type PickResult = { league_id: string; key?: string; name?: string; ok: boolean; error?: string; message?: string };

// What a refused /espn call says, for a person: the API's own sentence (`detail.message`), else a plain fallback.
export function espnMessage(body: unknown, fallback = "Something went wrong. Try again."): string {
  const detail = (body as { detail?: { message?: unknown } } | null)?.detail;
  return typeof detail?.message === "string" && detail.message ? detail.message : fallback;
}

// Which extension steps the Connect step shows, off the browser's user agent: an iPhone or iPad (Safari, the app from
// TestFlight), Safari on a Mac (the Mac app), Chrome or Edge on a computer (the Web Store), or anything else.
export type Platform = "ios" | "mac-safari" | "chrome" | "other";

export function platformOf(ua: string): Platform {
  if (/iPhone|iPad|iPod/.test(ua)) return "ios";
  if (/Android|Mobile/.test(ua)) return "other";
  if (/Chrome\/|Edg\//.test(ua) && !/OPR\//.test(ua)) return "chrome";
  if (/Macintosh/.test(ua) && /Safari\//.test(ua)) return "mac-safari";
  return "other";
}

// Pasting the cookies needs a browser's developer tools: offered on a computer only.
export const canPaste = (p: Platform) => p === "chrome" || p === "mac-safari" || p === "other";

// The login the Connect step is waiting for: one that was not there when the step opened (Add an ESPN login), or on a
// first run any working one. The newest first; null while none has arrived.
export function arrivedAccount(status: EspnStatus, before: number[], firstRun: boolean): EspnAccount | null {
  const fresh = status.accounts.filter((a) => a.connected && (firstRun || !before.includes(a.id)));
  if (!fresh.length) return null;
  return [...fresh].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
}

// Adding a login, the one that arrived instead was one already connected (the extension sent it again): it is stored
// afresh, so its `updated_at` moved since the step opened. Null when none did.
export function resentAccount(status: EspnStatus, before: { id: number; updated_at: string }[]): EspnAccount | null {
  return status.accounts.find((a) => before.some((b) => b.id === a.id && b.updated_at !== a.updated_at)) ?? null;
}

// How Settings names a login: by its place, first connected first ("ESPN login 2").
export const loginLabel = (accounts: EspnAccount[], id: number) => `ESPN login ${accounts.findIndex((a) => a.id === id) + 1}`;

// Settings' leagues under each login, in the logins' order, each list by name.
export function byAccount(accounts: EspnAccount[], leagues: LeagueChoice[]): { account: EspnAccount; leagues: LeagueChoice[] }[] {
  return accounts.map((account) => ({
    account,
    leagues: leagues.filter((l) => l.account === account.id).sort((a, b) => a.name.localeCompare(b.name)),
  }));
}

// A league the wizard may tick: one ESPN lets this login pick that is not in FFL for the user already.
export const pickable = (l: LeagueChoice) => l.ok && !l.joined;

// A team's mark when ESPN gives no logo: its initials, up to three.
export function initials(name: string | null): string {
  const words = (name ?? "").replace(/['’]s\b/g, "").split(/\s+/).filter((w) => /^[A-Za-z0-9]/.test(w));
  return words.slice(0, 3).map((w) => w[0].toUpperCase()).join("") || "?";
}
