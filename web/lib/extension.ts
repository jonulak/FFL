// The FFL extension in this browser, from the Connect step (Jonathan 2026-10-29: the site checks the extension works,
// links it with one tap, then sends the user to ESPN). The extension's content script runs on this site too
// (extension/ffl/src/content.ts onFfl) and talks by window.postMessage: `ffl-ext` messages from this window only.
// `hello` → `here` {paired}; `link` → `code` (a pairing the extension started) or `error`; `linked` → `done`.

export type ExtMessage =
  | { type: "here"; paired: boolean }
  | { type: "code"; code: string }
  | { type: "error"; message: string }
  | { type: "done"; paired: boolean };

// An extension message, or null for anything else on the window (another frame, another origin, page noise).
export function extMessage(data: unknown, source: unknown, origin: string, self: unknown, ownOrigin: string): ExtMessage | null {
  if (source !== self || origin !== ownOrigin) return null;
  const d = data as { "ffl-ext"?: unknown; type?: unknown; paired?: unknown; code?: unknown; message?: unknown } | null;
  if (!d || typeof d !== "object" || d["ffl-ext"] !== true) return null;
  if (d.type === "here") return { type: "here", paired: d.paired === true };
  if (d.type === "done") return { type: "done", paired: d.paired === true };
  if (d.type === "code" && typeof d.code === "string" && d.code) return { type: "code", code: d.code };
  if (d.type === "error") return { type: "error", message: typeof d.message === "string" ? d.message : "The extension didn't answer." };
  return null;
}

// Say `type` to the extension and wait for the first reply of one of `want`; null after `ms`.
function exchange(type: string, want: ExtMessage["type"][], ms: number): Promise<ExtMessage | null> {
  return new Promise((resolve) => {
    const done = (m: ExtMessage | null) => { window.removeEventListener("message", on); window.clearTimeout(t); resolve(m); };
    const on = (e: MessageEvent) => {
      const m = extMessage(e.data, e.source, e.origin, window, window.location.origin);
      if (m && want.includes(m.type)) done(m);
    };
    const t = window.setTimeout(() => done(null), ms);
    window.addEventListener("message", on);
    window.postMessage({ "ffl-ext": true, type }, window.location.origin);
  });
}

// Is the extension on in this browser, with this site allowed? null when nothing answers.
export async function findExtension(ms = 1500): Promise<{ paired: boolean } | null> {
  const m = await exchange("hello", ["here"], ms);
  return m?.type === "here" ? { paired: m.paired } : null;
}

// Link this browser to the signed-in user: the extension starts a pairing, `confirm` (the page's own session) takes
// its code, the extension then collects its token and sends the ESPN login if the browser has one. Throws a sentence.
export async function linkExtension(confirm: (code: string) => Promise<void>): Promise<void> {
  const m = await exchange("link", ["code", "error"], 15_000);
  if (!m) throw new Error("The extension didn't answer. Reload the page and try again.");
  if (m.type === "error") throw new Error(m.message);
  if (m.type !== "code") throw new Error("The extension didn't answer. Reload the page and try again.");
  await confirm(m.code);
  const d = await exchange("linked", ["done"], 15_000);
  if (d?.type !== "done" || !d.paired) throw new Error("The extension didn't finish connecting. Try again.");
}

// FFL opened from the Home Screen: Safari's extensions don't run here, and its cookies are its own.
export function homeScreenApp(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia?.("(display-mode: standalone)").matches
    || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}
