// Path (i): the cookies API, read three ways so the report tells which works where: get() with no store, getAll() with
// no store (the call Safari 17.5-18.1 answered [] from the background), and both again per store from
// getAllCookieStores() (the forums' workaround). Shapes only, never a value; nothing is sent anywhere.
const api = globalThis.browser ?? globalThis.chrome;
const URL_ = "https://www.espn.com/";
const NAMES = ["espn_s2", "SWID"];

function shape(c) {
  if (!c) return { present: false };
  return { present: true, length: c.value.length, braces: c.value.startsWith("{") && c.value.endsWith("}"),
           httpOnly: c.httpOnly, domain: c.domain, store: c.storeId };
}

async function attempt(label, fn) {
  try {
    return { label, ...(await fn()) };
  } catch (e) {
    return { label, error: String(e && e.message || e) };
  }
}

async function readApi() {
  const out = [];
  out.push(await attempt("get, no store", async () => {
    const r = {};
    for (const n of NAMES) r[n] = shape(await api.cookies.get({ url: URL_, name: n }));
    return r;
  }));
  out.push(await attempt("getAll(domain espn.com), no store", async () => {
    const all = await api.cookies.getAll({ domain: "espn.com" });
    const r = { count: all.length };
    for (const n of NAMES) r[n] = shape(all.find((c) => c.name === n));
    return r;
  }));
  let stores = [];
  out.push(await attempt("getAllCookieStores", async () => {
    stores = await api.cookies.getAllCookieStores();
    return { stores: stores.map((s) => s.id) };
  }));
  for (const s of stores) {
    out.push(await attempt(`get, store ${s.id}`, async () => {
      const r = {};
      for (const n of NAMES) r[n] = shape(await api.cookies.get({ url: URL_, name: n, storeId: s.id }));
      return r;
    }));
    out.push(await attempt(`getAll(domain espn.com), store ${s.id}`, async () => {
      const all = await api.cookies.getAll({ domain: "espn.com", storeId: s.id });
      const r = { count: all.length };
      for (const n of NAMES) r[n] = shape(all.find((c) => c.name === n));
      return r;
    }));
  }
  return out;
}

// onChanged: does the background hear a login or logout at all (it may never wake on Safari)?
api.cookies.onChanged.addListener(async (info) => {
  if (!NAMES.includes(info.cookie.name)) return;
  const { changes = [] } = await api.storage.local.get("changes");
  changes.unshift({ at: new Date().toISOString(), name: info.cookie.name, removed: info.removed, cause: info.cause });
  await api.storage.local.set({ changes: changes.slice(0, 10) });
});

api.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.kind === "page") {
    api.storage.local.set({ page: msg });
    return false;
  }
  if (msg.kind === "report") {
    readApi().then(async (apiReads) => {
      const { page = null, changes = [] } = await api.storage.local.get(["page", "changes"]);
      reply({ ua: navigator.userAgent, at: new Date().toISOString(), apiReads, page, changes });
    });
    return true;  // the reply is async
  }
  return false;
});
