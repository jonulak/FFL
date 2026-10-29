// One content script for both sites the extension runs on (the Safari wrapper bundles exactly five files).
//
// On espn.com: tell the background the page loaded or came back into view (a pending pairing is polled then), and
// watch the login while the tab is in view, so a sign-in in place reaches FFL with no reload and no popup (Jonathan
// 2026-10-28): every 3 s the cookies are compared in memory and the background hears only of a changed, signed-in
// login. Answers the popup's Send now with this page's cookies.
//
// On FFL's own site: answer the Connect step (web/lib/extension.ts), which links this browser with one tap instead of
// a typed code (Jonathan 2026-10-29). The page and this script talk by window.postMessage, `ffl-ext` messages from
// this window only: `hello` → `here` {paired}; `link` → `code` {code} (a pairing the background started; the page
// confirms it under its own session) or `error`; `linked` → `done` {paired, last}. Nothing of ESPN's crosses here.
import { api, FFL_ORIGIN, type Msg } from "./env";
import { ChangeWatch, fromCookieString } from "./pure";

const EVERY_MS = 3000;

function tell(msg: Msg): void {
  api.runtime.sendMessage(msg).catch(() => { /* the extension was reloaded or is off: nothing to tell */ });
}

function ask<T>(msg: Msg): Promise<T | null> {
  return (api.runtime.sendMessage(msg) as Promise<T>).catch(() => null);
}

function onEspn(): void {
  const watch = new ChangeWatch();

  const check = () => {
    if (document.visibilityState !== "visible") return;
    const cookies = fromCookieString(document.cookie);
    if (watch.seen(cookies)) tell({ kind: "page", cookies });
  };

  // The page loaded, or came back into view (on an iPhone: back in Safari after typing the code in FFL's Home Screen
  // app, where nothing of the extension runs): the background polls a pending pairing then, and sends a changed login.
  const arrive = () => {
    if (document.visibilityState !== "visible") return;
    const cookies = fromCookieString(document.cookie);
    watch.seen(cookies);
    tell({ kind: "page", cookies });
  };

  arrive();
  window.setInterval(check, EVERY_MS);
  document.addEventListener("visibilitychange", arrive);
  window.addEventListener("focus", check);

  api.runtime.onMessage.addListener((msg: Msg, _sender, reply) => {
    if (msg.kind === "read") reply(fromCookieString(document.cookie));
    return false;
  });
}

function onFfl(): void {
  const answer = (body: Record<string, unknown>) => window.postMessage({ "ffl-ext": true, ...body }, FFL_ORIGIN);
  const here = async () => answer({ type: "here", paired: Boolean((await ask<{ paired: boolean }>({ kind: "state" }))?.paired) });

  window.addEventListener("message", (e: MessageEvent) => {
    if (e.source !== window || e.origin !== FFL_ORIGIN) return;
    const d = e.data as { "ffl-ext"?: unknown; type?: unknown } | null;
    if (!d || d["ffl-ext"] !== true) return;
    if (d.type === "hello") void here();
    else if (d.type === "link") {
      void ask<{ code?: string; error?: string }>({ kind: "link" }).then((r) =>
        answer(r?.code ? { type: "code", code: r.code } : { type: "error", message: r?.error ?? "The extension didn't answer. Reload the page." }));
    } else if (d.type === "linked") {
      void ask<{ paired: boolean; last: unknown }>({ kind: "linked" }).then((r) =>
        answer({ type: "done", paired: Boolean(r?.paired), last: r?.last ?? null }));
    }
  });
  void here();   // unasked too: the page may have said hello before this script ran
}

if (location.origin === FFL_ORIGIN) onFfl();
else onEspn();
