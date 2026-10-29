// The background: pairs with FFL (the code it shows, the poll that brings the device token), reads ESPN's two
// cookies and sends them to FFL when the login changed. An MV3 worker sleeps between events and keeps no timer, so
// every wake does what is due: a page ping polls a pending pairing and sends a changed login; the popup polls while
// open. Never logs or stores a value: storage.local holds the token, the pairing, a hash of the last login sent and
// the last answer (pure.ts `Stored`).
import { api, FFL_ORIGIN, type Msg } from "./env";
import { distinctLogins, pairHash, pickStore, sendOutcome, signedIn, type Pair, type Partial2, type Pairing, type StoreRead,
         type Stored } from "./pure";

const ESPN_URL = "https://www.espn.com/";

async function load(): Promise<Stored> {
  return (await api.storage.local.get(["token", "pairing", "lastHash", "last"])) as Stored;
}

async function save(patch: Partial<Stored>, drop: (keyof Stored)[] = []): Promise<void> {
  if (Object.keys(patch).length) await api.storage.local.set(patch);
  if (drop.length) await api.storage.local.remove(drop);
}

// One FFL call: the status and the API's `detail.message` (never the body otherwise; no cookie is ever echoed back).
async function post(path: string, body: unknown, token?: string): Promise<{ status: number; json: Record<string, unknown> | null; message: string | null }> {
  try {
    const r = await fetch(`${FFL_ORIGIN}/api${path}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body ?? {}),
      credentials: "omit",
    });
    let json: Record<string, unknown> | null = null;
    try { json = await r.json(); } catch { json = null; }
    const detail = (json?.detail ?? null) as { message?: unknown } | null;
    return { status: r.status, json, message: typeof detail?.message === "string" ? detail.message : null };
  } catch {
    return { status: 0, json: null, message: null };
  }
}

async function startPairing(): Promise<string | null> {
  const r = await post("/espn/pair/start", {});
  if (r.status !== 200 || !r.json) return r.message ?? "FFL didn't answer. Try again in a minute.";
  const { code, poll_secret, expires_in } = r.json as { code: string; poll_secret: string; expires_in: number };
  const pairing: Pairing = { code, poll_secret, expires_at: Date.now() + expires_in * 1000 };
  await save({ pairing });
  return null;
}

// A pending pairing asked once: still pending, or the token (a new pairing replaces an old token), or gone.
async function pollPairing(): Promise<void> {
  const { pairing } = await load();
  if (!pairing) return;
  if (pairing.expires_at <= Date.now()) { await save({}, ["pairing"]); return; }
  const r = await post("/espn/pair/poll", { poll_secret: pairing.poll_secret });
  if (r.status === 410) { await save({}, ["pairing"]); return; }
  if (r.status !== 200 || !r.json || r.json.status !== "connected") return;
  await save({ token: String(r.json.device_token) }, ["pairing", "lastHash", "last"]);
  // the login goes at once, but behind: Safari may first ask the user to allow espn.com, and the FFL page waiting on
  // `linked` must hear it's linked without waiting on that (2026-10-29, simulator)
  void sync(null, false);
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
  const { token, lastHash } = await load();
  if (!token) return false;
  const login = await readLogin(page);
  if (!login) return false;
  const hash = await pairHash(login);
  if (!force && hash === lastHash) return true;
  const r = await post("/espn/cookies", { espn_s2: login.espn_s2, swid: login.swid, others: login.others ?? [] }, token);
  const out = sendOutcome(r.status, r.message);
  const last = { at: Date.now(), ok: out.ok, message: out.message };
  if (out.unpaired) await save({ last }, ["token", "lastHash"]);
  else if (out.remember) await save({ last, lastHash: hash });
  else await save({ last });
  return true;
}

// The popup asks the page it was opened on for its cookies (the document.cookie fallback): the active espn.com tab's
// content script answers; any other tab, nothing.
api.runtime.onMessage.addListener((msg: Msg, _sender, reply) => {
  (async () => {
    switch (msg.kind) {
      case "page":        // a content script: an ESPN page loaded, or its login changed
        await pollPairing();
        await sync(msg.cookies ?? null, false);
        return null;
      case "start":
        return { error: await startPairing() };
      case "poll":
        await pollPairing();
        return null;
      case "state":       // the FFL site's content script: is this browser linked?
        return { paired: Boolean((await load()).token) };
      case "link": {      // the FFL site asks to link: a pairing whose code goes to the page, never the screen
        const error = await startPairing();
        const { pairing } = await load();
        return error || !pairing ? { error: error ?? "The extension couldn't start. Try again." } : { code: pairing.code };
      }
      case "linked": {    // the page confirmed the code: take the token now, and send the login if ESPN has one
        await pollPairing();
        const { token, last } = await load();
        return { paired: Boolean(token), last: last ?? null };
      }
      case "cancel":
        await save({}, ["pairing"]);
        return null;
      case "send":
        return { found: await sync(msg.cookies ?? null, true) };
      case "unpair":
        await save({}, ["token", "lastHash", "last", "pairing"]);
        return null;
    }
  })().then(reply, () => reply(null));
  return true;   // the reply is async
});
