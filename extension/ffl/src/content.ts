// The content script, on espn.com only: tell the background the page loaded or came back into view (a pending pairing
// is polled then), and watch the login while the tab is in view, so a sign-in in place reaches FFL with no reload and
// no popup (Jonathan 2026-10-28): every 3 s the cookies are compared in memory and the background hears only of a
// changed, signed-in login. Answers the popup's Send now with this page's cookies.
import { api, type Msg } from "./env";
import { ChangeWatch, fromCookieString } from "./pure";

const EVERY_MS = 3000;

function tell(msg: Msg): void {
  api.runtime.sendMessage(msg).catch(() => { /* the extension was reloaded or is off: nothing to tell */ });
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

onEspn();
