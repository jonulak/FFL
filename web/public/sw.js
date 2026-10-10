// Web Push for the season UI (ffl/notify.py sends {title, body, url, tag}). A tap focuses an open FFL window
// and takes it to the notice's page, or opens one.

// Take over the page that registered us: an uncontrolled window cannot be navigate()d from here.
// A new worker takes over without waiting for every window to close.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

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
// itself: every open window is told now, and a page that loads within REPLAY_MS says hello and is told then.
const REPLAY_MS = 30_000;
let pending = null;

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  pending = { url, at: Date.now() };
  event.waitUntil(
    (async () => {
      const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      if (wins.length === 0) return self.clients.openWindow(url);
      for (const w of wins) w.postMessage({ type: "open", url });
      try {
        await wins[0].focus();
      } catch {
        // focus() needs the click's user activation; the message has been sent either way
      }
    })(),
  );
});

self.addEventListener("message", (event) => {
  const type = event.data?.type;
  if (type === "opened") pending = null;
  else if (type === "hello" && pending && Date.now() - pending.at < REPLAY_MS) {
    event.source?.postMessage({ type: "open", url: pending.url });
  }
});
