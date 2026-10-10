// The VAPID public key arrives base64url; PushManager.subscribe wants the raw bytes.
export function urlBase64ToUint8Array(s: string): Uint8Array<ArrayBuffer> {
  const b64 = (s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

// The /live stream carries a league-move count: `m<n>` on a move, and `<version> m<n>` as each connection's opening
// message (ffl/live.py stream_open). A page not on the live hook refreshes when the count moves past the one it
// last saw — on the move tick, or on the reconnect after the phone slept through it. Points ticks never count.
export function movesStep(last: number | null, data: unknown): { moves: number | null; refresh: boolean } {
  const m = typeof data === "string" ? /(?:^| )m(\d+)$/.exec(data) : null;
  if (!m) return { moves: last, refresh: false };
  const moves = Number(m[1]);
  return { moves, refresh: last !== null && moves !== last };
}

// A notification tap arrives as the worker's {type: "open", url} message (web/public/sw.js): the page navigates
// itself, since iOS's Home Screen app ignores the worker's own navigate()/openWindow(). Same-origin only.
export function openRequest(data: unknown, origin: string): string | null {
  const d = data as { type?: unknown; url?: unknown } | null;
  if (!d || d.type !== "open" || typeof d.url !== "string") return null;
  try {
    const u = new URL(d.url, origin);
    return u.origin === origin ? u.pathname + u.search + u.hash : null;
  } catch {
    return null;
  }
}

export type PushEnv = { hasServiceWorker: boolean; hasPushManager: boolean; standalone: boolean; ios: boolean };

// iOS offers Web Push only to a Home Screen app (16.4+): in Safari proper PushManager is missing.
export function pushSupport(env: PushEnv): "unsupported" | "needs-install" | "ready" {
  if (!env.hasServiceWorker) return "unsupported";
  if (env.ios && !env.standalone) return "needs-install";
  return env.hasPushManager ? "ready" : "unsupported";
}

export function readPushEnv(): PushEnv {
  const nav = navigator as Navigator & { standalone?: boolean };
  return {
    hasServiceWorker: "serviceWorker" in navigator,
    hasPushManager: "PushManager" in window,
    standalone: window.matchMedia("(display-mode: standalone)").matches || nav.standalone === true,
    ios: /iPad|iPhone|iPod/.test(navigator.userAgent),
  };
}

export const isHm = (s: string) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s);
