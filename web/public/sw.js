// Web Push for the season UI (ffl/notify.py sends {title, body, url, tag}). A tap focuses an open FFL window
// and takes it to the notice's page, or opens one. Also keeps ESPN's images for a cold open.

// ESPN's headshots and club marks (web/lib/teams.ts), kept here so a cold open draws them without the network: the
// CDN's own cache lasts a day and iOS clears a Home Screen app's often. A kept image is served at once, and one older
// than IMG_FRESH_MS is fetched again behind it. The CDN answers CORS, so a response is readable: only a 200 is kept,
// and a missing portrait's 404 passes through to the page's fallback. The oldest go past IMG_MAX.
const IMG_CACHE = "espn-img-v1";
const IMG_HOST = "https://a.espncdn.com/";
const IMG_FRESH_MS = 7 * 24 * 3600_000;
const IMG_MAX = 1000;
const STAMP = "x-ffl-kept";

// Take over the page that registered us: an uncontrolled window cannot be navigate()d from here.
// A new worker takes over without waiting for every window to close.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
  event.waitUntil(
    (async () => {
      for (const k of await caches.keys()) if (k.startsWith("espn-img-") && k !== IMG_CACHE) await caches.delete(k);
      await self.clients.claim();
    })(),
  ),
);

async function keepImage(cache, url) {
  const res = await fetch(url, { mode: "cors", credentials: "omit" });
  if (!res.ok) return res;
  const headers = new Headers(res.headers);
  headers.set(STAMP, String(Date.now()));
  await cache.put(url, new Response(await res.clone().blob(), { status: res.status, statusText: res.statusText, headers }));
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - IMG_MAX))) await cache.delete(k);
  return res;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET" || !req.url.startsWith(IMG_HOST)) return;
  event.respondWith(
    (async () => {
      const cache = await caches.open(IMG_CACHE);
      const hit = await cache.match(req.url);
      if (!hit) return keepImage(cache, req.url).catch(() => fetch(req));
      if (Date.now() - Number(hit.headers.get(STAMP) || 0) > IMG_FRESH_MS) {
        event.waitUntil(keepImage(cache, req.url).catch(() => {}));
      }
      return hit;
    })(),
  );
});

self.addEventListener("push", (event) => {
  let d = {};
  if (event.data) {
    try {
      d = event.data.json();
    } catch {
      d = { title: "FFL", body: event.data.text() };
    }
  }
  event.waitUntil(
    self.registration.showNotification(d.title || "FFL", {
      body: d.body || "", tag: d.tag, renotify: Boolean(d.tag), icon: "/icon-192.png", badge: "/icon-192.png", data: { url: d.url || "/" },
    }),
  );
});

// The page a tap asked for, until a page confirms it landed ("opened"). iOS's Home Screen app ignores
// WindowClient.navigate() and may cold-start at start_url whatever openWindow() was given, so the page navigates
// itself: every open window is told now, and a page that says hello within REPLAY_MS (on load, or on coming back
// to the foreground) is told then. The click's waitUntil stays open until then, so the browser does not stop this
// worker, and `pending` with it, before the page asks.
const REPLAY_MS = 30_000;
let pending = null;

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const asked = new URL(event.notification.data?.url || "/", self.location.origin);
  const url = asked.origin === self.location.origin ? asked.href : new URL("/", self.location.origin).href;
  let landed;
  const done = new Promise((resolve) => { landed = resolve; });
  pending = { url, at: Date.now(), landed };
  setTimeout(landed, REPLAY_MS);
  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      if (wins.length === 0) await self.clients.openWindow(url);
      else {
        for (const w of wins) w.postMessage({ type: "open", url });
        try {
          await wins[0].focus();
        } catch {
          // focus() needs the click's user activation; the message has been sent either way
        }
      }
      await done;
    })(),
  );
});

self.addEventListener("message", (event) => {
  const type = event.data?.type;
  if (type === "opened" && pending) {
    pending.landed();
    pending = null;
  } else if (type === "hello" && pending && Date.now() - pending.at < REPLAY_MS) {
    event.source?.postMessage({ type: "open", url: pending.url });
  }
});
