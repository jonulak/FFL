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

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      try {
        const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
        for (const w of wins) {
          if ("focus" in w) {
            await w.focus();
            return await w.navigate(url);
          }
        }
      } catch {
        // navigate() rejects on a window we do not control: open one instead
      }
      return self.clients.openWindow(url);
    })(),
  );
});
