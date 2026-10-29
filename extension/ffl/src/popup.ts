// The popup: the disclosure and Connect (once per browser), the pairing code (re-shown on every open until FFL takes
// it or it lapses; polled every 2 s while open), and once paired the last answer from FFL with Send now. It shows
// what this browser knows only: the device token can do one thing, send the login, and never reads the account.
import { api, FFL_ORIGIN, FFL_SITE, type Msg } from "./env";
import { ago, viewOf, type Partial2, type Stored } from "./pure";

const POLL_MS = 2000;
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function ask<T = unknown>(msg: Msg): Promise<T> {
  return api.runtime.sendMessage(msg) as Promise<T>;
}

let timer: number | undefined;

async function render(): Promise<void> {
  const s = (await api.storage.local.get(["token", "pairing", "lastHash", "last"])) as Stored;
  const now = Date.now();
  const v = viewOf(s, now);
  $("disclose").hidden = v.kind !== "disclose";
  $("code-view").hidden = v.kind !== "code";
  $("paired").hidden = v.kind !== "paired";
  if (v.kind === "code") {
    $("code").textContent = v.code;
    $("left").textContent = `It lasts ${v.minutesLeft} more minute${v.minutesLeft === 1 ? "" : "s"}.`;
    if (timer === undefined) timer = window.setInterval(() => { void ask({ kind: "poll" }).then(render); }, POLL_MS);
  } else if (timer !== undefined) {
    window.clearInterval(timer);
    timer = undefined;
  }
  if (v.kind === "paired") {
    const last = v.last;
    $("last").textContent = last ? last.message : "Paired. Your ESPN login goes to FFL when you sign in to ESPN here.";
    $("last").className = last && !last.ok ? "bad" : "";
    $("last-at").textContent = last ? `Last checked ${ago(last.at, now)}` : "";
  }
}

// The FFL site is the extension's second host. Chrome grants it at install; Safari asks per site, so the click that
// starts pairing (a user gesture) asks for it when it isn't granted yet.
async function hostAllowed(): Promise<boolean> {
  const origins = [FFL_SITE];
  try {
    if (await api.permissions.contains({ origins })) return true;
    return await api.permissions.request({ origins });
  } catch {
    return true;   // a browser without the permissions API: the fetch itself will tell
  }
}

async function connect(): Promise<void> {
  const button = $<HTMLButtonElement>("connect");
  button.disabled = true;
  $("connect-error").textContent = "";
  try {
    if (!(await hostAllowed())) {
      $("connect-error").textContent = `Allow FFL on ${new URL(FFL_ORIGIN).host} to connect.`;
      return;
    }
    const r = await ask<{ error: string | null }>({ kind: "start" });
    if (r?.error) $("connect-error").textContent = r.error;
  } finally {
    button.disabled = false;
    await render();
  }
}

// Send now: the active tab's cookies when it is an espn.com page (its content script answers), else the cookies API.
async function sendNow(): Promise<void> {
  const button = $<HTMLButtonElement>("send");
  button.disabled = true;
  try {
    let cookies: Partial2 | undefined;
    try {
      const [tab] = await api.tabs.query({ active: true, currentWindow: true });
      if (tab?.id !== undefined) cookies = await api.tabs.sendMessage(tab.id, { kind: "read" } satisfies Msg);
    } catch { cookies = undefined; }
    const r = await ask<{ found: boolean }>({ kind: "send", cookies });
    if (!r?.found) {
      $("last").textContent = "You're not signed in to ESPN in this browser. Sign in at espn.com; your login goes to FFL by itself.";
      $("last").className = "bad";
      $("last-at").textContent = "";
      return;
    }
    await render();
  } finally {
    button.disabled = false;
  }
}

// Connecting happens on FFL's own site (Jonathan 2026-10-29): it finds this extension and links it with one tap, for
// whoever is signed in there. The site must be allowed first (Safari asks per site), so this click asks for it.
async function openFfl(): Promise<void> {
  $("connect-error").textContent = "";
  if (!(await hostAllowed())) {
    $("connect-error").textContent = `Allow FFL on ${new URL(FFL_ORIGIN).host} to connect.`;
    return;
  }
  await api.tabs.create({ url: `${FFL_ORIGIN}/welcome?from=extension` });
  window.close();
}

$("host").textContent = new URL(FFL_ORIGIN).host;
$("open").addEventListener("click", () => { void openFfl(); });
$("connect").addEventListener("click", () => { void connect(); });   // the code: a browser where FFL isn't signed in
$("again").addEventListener("click", () => { void openFfl(); });
$("cancel").addEventListener("click", () => { void ask({ kind: "cancel" }).then(render); });
$("unpair").addEventListener("click", () => { void ask({ kind: "unpair" }).then(render); });
$("send").addEventListener("click", () => { void sendNow(); });
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("code").textContent ?? "");
    $("copy").textContent = "Copied";
    window.setTimeout(() => { $("copy").textContent = "Copy"; }, 1500);
  } catch { /* select-all on the code still works */ }
});
api.storage.onChanged.addListener(() => { void render(); });

void ask({ kind: "poll" }).then(render, render);
