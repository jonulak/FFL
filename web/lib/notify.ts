import type { Notice } from "@/lib/api";

// The VAPID public key arrives base64url; PushManager.subscribe wants the raw bytes.
export function urlBase64ToUint8Array(s: string): Uint8Array<ArrayBuffer> {
  const b64 = (s + "=".repeat((4 - (s.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
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

export type PushEnv ={ hasServiceWorker: boolean; hasPushManager: boolean; standalone: boolean; ios: boolean };

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

export function noticeState(n: Notice): string {
  if (!n.sent_at) return "held";
  return { sent: "sent", off: "switched off", expired: "too old" }[n.outcome ?? ""] ?? n.outcome ?? "";
}
