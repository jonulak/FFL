// The background: pairs with FFL (from the open FFL tab, its session confirming; or by a code the user types), reads
// ESPN's two cookies and sends them to FFL when the login changed. An MV3 worker sleeps between events and keeps no
// timer, so every wake does what is due: a page ping polls a pending pairing and sends a changed login; the popup polls
// while open. Never logs or stores a value: storage.local holds the server, the token, the pairing, a hash of the last
// login sent and the last answer (pure.ts `Stored`).
import { api, type Msg } from "./env";
import { distinctLogins, pairHash, pickStore, sendOutcome, signedIn, type Pair, type Partial2, type Pairing, type StoreRead,
         type Stored } from "./pure";

const ESPN_URL = "https://www.espn.com/";
// The one server the builds before 0.2 knew (baked in): an install paired then has a token and no `server`.
const OLD_SERVER = "https://ffl.whitebridgelabs.com";
const NO_ANSWER = "FFL didn't answer. Try again in a minute.";

const migrated = (async () => {
  const s = (await api.storage.local.get(["token", "server"])) as Stored;
  if (s.token && !s.server) await api.storage.local.set({ server: OLD_SERVER });
})().catch(() => { /* storage unreadable: the next wake tries again */ });

async function load(): Promise<Stored> {
  await migrated;
  return (await api.storage.local.get(["server", "token", "pairing", "lastHash", "last"])) as Stored;
}

async function save(patch: Partial<Stored>, drop: (keyof Stored)[] = []): Promise<void> {
  if (Object.keys(patch).length) await api.storage.local.set(patch);
  if (drop.length) await api.storage.local.remove(drop);
}

// One FFL call: the status and the API's `detail.message` (never the body otherwise; no cookie is ever echoed back).
// `credentials: "include"` only for the confirm, which rides on the tab's FFL session.
async function post(server: string, path: string, body: unknown, token?: string, credentials: RequestCredentials = "omit"):
    Promise<{ status: number; json: Record<string, unknown> | null; message: string | null }> {
  try {
    const r = await fetch(`${server}/api${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body ?? {}),
      credentials,
    });
    let json: Record<string, unknown> | null = null;
    try { json = await r.json(); } catch { json = null; }
    const detail = (json?.detail ?? null) as { message?: unknown } | null;
    return { status: r.status, json, message: typeof detail?.message === "string" ? detail.message : null };
  } catch {
    return { status: 0, json: null, message: null };
  }
}

// A pairing whose code the user types in FFL (the fallback), kept with its server until the token comes.
async function pairCode(server: string): Promise<string | null> {
  const r = await post(server, "/espn/pair/start", {});
  if (r.status !== 200 || !r.json) return r.message ?? NO_ANSWER;
  const { code, poll_secret, expires_in } = r.json as { code: string; poll_secret: string; expires_in: number };
  const pairing: Pairing = { code, poll_secret, expires_at: Date.now() + expires_in * 1000, server };
  await save({ pairing });
  return null;
}

// The token arrived: this server and token replace any before, and the login goes at once, but behind (Safari may first
// ask the user to allow espn.com; 2026-10-29, simulator).
async function paired(server: string, token: string): Promise<void> {
  await save({ server, token }, ["pairing", "lastHash", "last"]);
  void sync(null, false);
}

// Pair with the FFL open in the tab: start a pairing, confirm its code under the tab's own session (the popup was
// granted the origin first), then poll once for the token. No code on screen.
async function connectTab(origin: string): Promise<string | null> {
  const start = await post(origin, "/espn/pair/start", {});
  if (start.status !== 200 || !start.json) return start.message ?? NO_ANSWER;
  const { code, poll_secret } = start.json as { code: string; poll_secret: string };
  const ok = await post(origin, "/espn/pair/confirm", { code }, undefined, "include");
  if (ok.status === 401) return "Sign in to FFL in this tab first, or pair with a code instead.";
  if (ok.status !== 200) return ok.message ?? NO_ANSWER;
  const poll = await post(origin, "/espn/pair/poll", { poll_secret });
  if (poll.status !== 200 || poll.json?.status !== "connected") return poll.message ?? NO_ANSWER;
  await paired(origin, String(poll.json.device_token));
  return null;
}

// A pending pairing asked once: still pending, or the token (a new pairing replaces an old token), or gone.
async function pollPairing(): Promise<void> {
  const { pairing } = await load();
  if (!pairing) return;
  // a pairing from before 0.2 names no server: let it go
  if (pairing.expires_at <= Date.now() || !pairing.server) { await save({}, ["pairing"]); return; }
  const r = await post(pairing.server, "/espn/pair/poll", { poll_secret: pairing.poll_secret });
  if (r.status === 410) { await save({}, ["pairing"]); return; }
  if (r.status !== 200 || !r.json || r.json.status !== "connected") return;
  await paired(pairing.server, String(r.json.device_token));
}

// The login's cookie of a name: the one set for all of .espn.com (ESPN's sign-in sets it there), else the first.
function best(cs: chrome.cookies.Cookie[]): string | undefined {
  return (cs.find((c) => c.domain === ".espn.com") ?? cs[0])?.value || undefined;
}

// What each cookie store holds for espn.com: Safari answers only per storeId (the 6.3.1 spike). Every SWID, not
// `cookies.get`'s one: of two with a name it returns the oldest, which can be a stale one beside the login's.
async function readStores(): Promise<StoreRead[]> {
  const stores = await api.cookies.getAllCookieStores();
  const reads: StoreRead[] = [];
  for (const s of stores) {
    const [s2, sw] = await Promise.all([
      api.cookies.getAll({ url: ESPN_URL, name: "espn_s2", storeId: s.id }),
      api.cookies.getAll({ url: ESPN_URL, name: "SWID", storeId: s.id }),
    ]);
    reads.push({ storeId: s.id, espn_s2: best(s2), swid: best(sw), swids: sw.map((c) => c.value).filter(Boolean) });
  }
  return reads;
}

// The login to send: the cookies API's store holding the page's SWID, else the page's own document.cookie values.
async function readLogin(page: Partial2 | null): Promise<Pair | null> {
  let reads: StoreRead[] = [];
  try { reads = await readStores(); } catch { reads = []; }
  const picked = pickStore(reads, page);
  const swids = page?.swids ?? [];
  if (picked || !page || !signedIn(page)) return picked;
  const others = distinctLogins([page.swid, ...swids]).slice(1);
  return others.length ? { espn_s2: page.espn_s2, swid: page.swid, others } : { espn_s2: page.espn_s2, swid: page.swid };
}

// Send the login if it changed since the last one sent (`force`: the popup's Send now sends it anyway). Returns
// whether a signed-in login was found.
async function sync(page: Partial2 | null, force: boolean): Promise<boolean> {
  const { server, token, lastHash } = await load();
  if (!token || !server) return false;
  const login = await readLogin(page);
  if (!login) return false;
  const hash = await pairHash(login);
  if (!force && hash === lastHash) return true;
  const r = await post(server, "/espn/cookies", { espn_s2: login.espn_s2, swid: login.swid, others: login.others ?? [] }, token);
  const out = sendOutcome(r.status, r.message);
  const last = { at: Date.now(), ok: out.ok, message: out.message };
  if (out.unpaired) await save({ last }, ["token", "lastHash"]);
  else if (out.remember) await save({ last, lastHash: hash });
  else await save({ last });
  return true;
}

api.runtime.onMessage.addListener((msg: Msg, _sender, reply) => {
  (async () => {
    switch (msg.kind) {
      case "page":        // a content script: an ESPN page loaded, or its login changed
        await pollPairing();
        await sync(msg.cookies ?? null, false);
        return null;
      case "connect-tab":
        return { error: await connectTab(msg.origin) };
      case "pair-code":
        return { error: await pairCode(msg.server) };
      case "start": {     // the code fallback against the server already known
        const { server } = await load();
        return { error: server ? await pairCode(server) : "Open your FFL app in this tab, then click here." };
      }
      case "poll":
        await pollPairing();
        return null;
      case "cancel":
        await save({}, ["pairing"]);
        return null;
      case "send":
        return { found: await sync(msg.cookies ?? null, true) };
      case "unpair":
        await save({}, ["server", "token", "lastHash", "last", "pairing"]);
        return null;
    }
  })().then(reply, () => reply(null));
  return true;   // the reply is async
});
