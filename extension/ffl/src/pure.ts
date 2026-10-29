// The extension's decisions, pure (tested in pure.test.ts): reading the two cookies out of a cookie string, choosing
// the cookie store that holds the page's login, noticing a changed login, what a send's answer means, and which view
// the popup shows. Never logs or stores a value: only a SHA-256 of the pair is kept (`pairHash`).

// `others`: every other signed-in-looking SWID the browser holds for ESPN. ESPN's own scripts write SWID, so a
// browser can hold several (an old visitor's beside the login's: the cookies API hands back the oldest of a name), and
// nothing here tells the login's from a stale one: FFL tries `swid`, then each of these, until ESPN knows one.
export type Pair = { espn_s2: string; swid: string; others?: string[] };
export type Partial2 = { espn_s2?: string; swid?: string; swids?: string[] };

// espn_s2 and SWID from a `document.cookie` string, exactly as the cookies hold them: never decoded (espn_s2 is
// URL-encoded, and FFL stores it as sent). `swids`: every SWID the string holds, when it holds more than one.
export function fromCookieString(s: string): Partial2 {
  const out: Partial2 = {};
  const swids: string[] = [];
  for (const part of s.split(";")) {
    const i = part.indexOf("=");
    if (i < 0) continue;
    const name = part.slice(0, i).trim();
    const value = part.slice(i + 1).trim();
    if (name === "espn_s2" && value) out.espn_s2 = value;
    else if (name === "SWID" && value) { out.swid = value; swids.push(value); }
  }
  if (swids.length > 1) out.swids = swids;
  return out;
}

// The braced SWIDs among `raw`, one per login however each was copied, in order.
export function distinctLogins(raw: (string | undefined)[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const s of raw) {
    if (!s || !signedIn({ espn_s2: "-", swid: s })) continue;
    const k = swidKey(s);
    if (!seen.has(k)) { seen.add(k); out.push(s); }
  }
  return out;
}

// One SWID however it was copied (percent-encoded or not, braces or not, any case): for comparing, never for sending.
export function swidKey(raw: string): string {
  let s = raw;
  try { s = decodeURIComponent(raw); } catch { /* not encoded */ }
  return s.trim().replace(/^\{|\}$/g, "").toUpperCase();
}

// A signed-in SWID is a braced GUID; an anonymous visitor's has no braces (ESPN sets one for everyone).
export function signedIn(p: Partial2): p is Pair {
  if (!p.espn_s2 || !p.swid) return false;
  let s = p.swid;
  try { s = decodeURIComponent(p.swid); } catch { /* not encoded */ }
  return /^\{[0-9A-Fa-f-]{36}\}$/.test(s.trim());
}

// What one cookie store holds (`cookies.getAllCookieStores`, then `cookies.get` per store: Safari answers nothing
// without a storeId, and keeps ESPN's cookies in its second persistent store).
// `swid` the login's best guess (the one on .espn.com), `swids` every SWID the store holds for ESPN.
export type StoreRead = { storeId: string; espn_s2?: string; swid?: string; swids?: string[] };

// The login to send: the store holding one of the page's SWIDs (Safari's private and normal stores can each hold one),
// else the one store that holds a signed-in pair, else none (ambiguous: more than one, and no page to tell them
// apart). `others` are the rest of that store's and the page's braced SWIDs, for FFL to try after `swid`.
export function pickStore(reads: StoreRead[], page: Partial2 | null): Pair | null {
  const full = reads.filter((r) => signedIn(r)) as (StoreRead & Pair)[];
  const pageSwids = distinctLogins([...(page?.swids ?? []), page?.swid]);
  const want = new Set(pageSwids.map(swidKey));
  const hit = (want.size ? full.find((r) => distinctLogins([r.swid, ...(r.swids ?? [])]).some((s) => want.has(swidKey(s)))) : undefined)
    ?? (full.length === 1 ? full[0] : undefined);
  if (!hit) return null;
  const others = distinctLogins([hit.swid, ...(hit.swids ?? []), ...pageSwids]).slice(1);
  return others.length ? { espn_s2: hit.espn_s2, swid: hit.swid, others } : { espn_s2: hit.espn_s2, swid: hit.swid };
}

// The pair's fingerprint: what is remembered of a login once sent (hex SHA-256), so the same login is never sent twice.
export async function pairHash(p: Pair): Promise<string> {
  const bytes = new TextEncoder().encode(`${swidKey(p.swid)}\n${p.espn_s2}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// The content script's change detector: true when the page shows a signed-in login it hasn't reported yet. Kept in
// memory only (the page's lifetime); the background's hash decides whether anything is sent.
export class ChangeWatch {
  private last: string | null = null;

  seen(p: Partial2): boolean {
    if (!signedIn(p)) return false;
    const key = `${swidKey(p.swid)}\n${p.espn_s2}`;
    if (key === this.last) return false;
    this.last = key;
    return true;
  }
}

// What a send's answer means. `remember`: the pair is done with (sent, or refused in a way the same pair would be again:
// 422), so the next page load doesn't resend it; a 429, a 5xx or no answer is retried on the next page load. `unpaired`:
// FFL no longer knows this extension (revoked, or the account disconnected every login).
export type SendOutcome = { ok: boolean; remember: boolean; unpaired: boolean; message: string };

export function sendOutcome(status: number, detailMessage: string | null): SendOutcome {
  if (status === 200) return { ok: true, remember: true, unpaired: false, message: "Your ESPN login is connected to FFL." };
  if (status === 401) {
    return { ok: false, remember: false, unpaired: true, message: "FFL no longer knows this browser. Connect again." };
  }
  if (status === 422) {
    return { ok: false, remember: true, unpaired: false, message: detailMessage ?? "ESPN refused this login." };
  }
  if (status === 429) {
    return { ok: false, remember: false, unpaired: false, message: detailMessage ?? "Too many tries. Wait a while." };
  }
  return { ok: false, remember: false, unpaired: false,
           message: detailMessage ?? "FFL didn't answer. It tries again on your next ESPN page." };
}

// A pairing in progress: the code to show, the secret to poll with, when it lapses (epoch ms).
export type Pairing = { code: string; poll_secret: string; expires_at: number };

export type Last = { at: number; ok: boolean; message: string };

// Everything the extension keeps (storage.local), never a cookie value.
export type Stored = { token?: string; pairing?: Pairing; lastHash?: string; last?: Last };

// The popup's view: the disclosure and Connect (not paired, no code), the code (a pairing alive), or paired.
export type View =
  | { kind: "disclose" }
  | { kind: "code"; code: string; minutesLeft: number }
  | { kind: "paired"; last: Last | null; pairing: false };

export function viewOf(s: Stored, now: number): View {
  if (s.pairing && s.pairing.expires_at > now) {
    return { kind: "code", code: s.pairing.code, minutesLeft: Math.max(1, Math.ceil((s.pairing.expires_at - now) / 60_000)) };
  }
  if (s.token) return { kind: "paired", last: s.last ?? null, pairing: false };
  return { kind: "disclose" };
}

// "just now", "4 min ago", "2 h ago", "3 days ago"
export function ago(at: number, now: number): string {
  const m = Math.floor((now - at) / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}
