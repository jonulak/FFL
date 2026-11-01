// The popup: connecting to the FFL open in this tab (its /health says FFL, its /auth/me who is signed in; Connect asks
// the browser for that origin, then the background pairs under the tab's session), or the fallback, a code shown for an
// address the user types; the code is re-shown on every open until FFL takes it or it lapses (polled every 2 s while
// open). An http(s) tab not yet granted shows Connect first: the click asks for the origin, then reads it. Once
// paired: the server, the last answer from FFL and Send now. It shows what this browser knows only: the device token
// can do one thing, send the login, and never reads the account.
import { api, type Msg } from "./env";
import { ago, originPattern, serverOf, tabView, typedServer, viewOf, type Partial2, type Stored, type TabView } from "./pure";

const POLL_MS = 2000;
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

function ask<T = unknown>(msg: Msg): Promise<T> {
  return api.runtime.sendMessage(msg) as Promise<T>;
}

let timer: number | undefined;
// "auto": what storage says (code, paired, else the tab); "tab": the tab even when paired (Connect to another);
// "form": the code fallback's address field
let mode: "auto" | "tab" | "form" = "auto";
let tab: { origin: string | null; state: TabView | null } = { origin: null, state: null };

async function getJson(url: string, credentials: RequestCredentials): Promise<unknown> {
  try {
    const r = await fetch(url, { credentials, signal: AbortSignal.timeout(5000) });
    return await r.json();
  } catch {
    return null;
  }
}

// What the tab's server says: /health needs no cookie; /me rides on the tab's session.
async function readServer(origin: string): Promise<TabView> {
  const [health, me] = await Promise.all([getJson(`${origin}/api/health`, "omit"), getJson(`${origin}/api/auth/me`, "include")]);
  return tabView(origin, true, health, me);
}

// The open tab's FFL, if it is one. Its /health and /me can be read only once the origin is granted (CORS and the
// session cookie follow the host permission, not activeTab), so an ungranted http(s) tab is asked about first.
async function readTab(): Promise<void> {
  let origin: string | null = null;
  try {
    const [t] = await api.tabs.query({ active: true, currentWindow: true });
    origin = t?.url ? serverOf(t.url) : null;
  } catch { origin = null; }
  let granted = false;
  if (origin) {
    try { granted = await api.permissions.contains({ origins: [originPattern(origin)] }); } catch { granted = true; }
  }
  tab = { origin, state: granted && origin ? await readServer(origin) : tabView(origin, false, null, null) };
}

function renderTab(): void {
  const s = tab.state;
  const host = tab.origin ? new URL(tab.origin).host : "";
  $("tab-disclose").hidden = s?.kind !== "ok";   // "your FFL" only once the tab is known to be one
  $("connect").hidden = s?.kind !== "ok" && s?.kind !== "ask";
  $("host").textContent = host;
  if (!s) {
    $("tab-title").textContent = "Connect this browser to FFL";
    $("tab-text").textContent = "Looking at this tab…";
  } else if (s.kind === "not-ffl") {
    $("tab-title").textContent = "Connect this browser to FFL";
    $("tab-text").textContent = "Open your FFL app in this tab, then click here.";
  } else if (s.kind === "ask") {
    $("tab-title").textContent = `Connect to ${host}`;
    $("tab-text").textContent = "The browser asks you to allow FFL on this site first.";
  } else if (s.kind === "signed-out") {
    $("tab-title").textContent = `Sign in to ${host}`;
    $("tab-text").textContent = "Sign in to FFL in this tab first, then click here again, or pair with a code instead.";
  } else {
    $("tab-title").textContent = `Connect to ${host}?`;
    $("tab-text").textContent = s.username ? `As ${s.username}.` : "";
  }
}

async function render(): Promise<void> {
  const s = (await api.storage.local.get(["server", "token", "pairing", "lastHash", "last"])) as Stored;
  const now = Date.now();
  const v = viewOf(s, now);
  const view = v.kind === "code" ? "code" : mode === "form" ? "form" : v.kind === "paired" && mode === "auto" ? "paired" : "tab";
  $("tab").hidden = view !== "tab";
  $("code-form").hidden = view !== "form";
  $("code-view").hidden = view !== "code";
  $("paired").hidden = view !== "paired";
  if (view === "tab") renderTab();
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
    $("paired-host").textContent = v.server ? new URL(v.server).host : "";
    $("last").textContent = last ? last.message : "Paired. Your ESPN login goes to FFL when you sign in to ESPN here.";
    $("last").className = last && !last.ok ? "bad" : "";
    $("last-at").textContent = last ? `Last checked ${ago(last.at, now)}` : "";
  }
}

// Ask the browser for a server's origin. Called first in a click handler: the request needs the user's gesture.
async function allow(origin: string): Promise<boolean> {
  try {
    return await api.permissions.request({ origins: [originPattern(origin)] });
  } catch {
    return true;   // a browser without the permissions API: the fetch itself will tell
  }
}

async function connect(): Promise<void> {
  const origin = tab.origin;
  if (!origin) return;
  const button = $<HTMLButtonElement>("connect");
  button.disabled = true;
  $("connect-error").textContent = "";
  try {
    if (!(await allow(origin))) {
      $("connect-error").textContent = `Allow FFL on ${new URL(origin).host} to connect, or pair with a code.`;
      return;
    }
    // first grant: read the server now, and go straight on only when it is an FFL signed in
    if (tab.state?.kind === "ask") {
      tab = { origin, state: await readServer(origin) };
      if (tab.state?.kind !== "ok") {
        // not an FFL after all: take back the standing grant the click just gave that site
        if (tab.state?.kind === "not-ffl") await api.permissions.remove({ origins: [originPattern(origin)] }).catch(() => false);
        return;
      }
    }
    const r = await ask<{ error: string | null }>({ kind: "connect-tab", origin });
    if (r?.error) $("connect-error").textContent = r.error;
    else mode = "auto";
  } finally {
    button.disabled = false;
    await render();
  }
}

async function startCode(e: Event): Promise<void> {
  e.preventDefault();
  const server = typedServer($<HTMLInputElement>("server").value);
  $("start-error").textContent = "";
  if (!server) { $("start-error").textContent = "Type your FFL's address, like ffl.example.com."; return; }
  const button = $<HTMLButtonElement>("start");
  button.disabled = true;
  try {
    if (!(await allow(server))) {
      $("start-error").textContent = `Allow FFL on ${new URL(server).host} to pair with it.`;
      return;
    }
    const r = await ask<{ error: string | null }>({ kind: "pair-code", server });
    if (r?.error) $("start-error").textContent = r.error;
    else mode = "auto";
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
      const [t] = await api.tabs.query({ active: true, currentWindow: true });
      if (t?.id !== undefined) cookies = await api.tabs.sendMessage(t.id, { kind: "read" } satisfies Msg);
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

$("connect").addEventListener("click", () => { void connect(); });
$("use-code").addEventListener("click", () => {
  mode = "form";
  if (tab.origin && tab.state?.kind !== "not-ffl") $<HTMLInputElement>("server").value = tab.origin;
  void render().then(() => $("server").focus());
});
$("server-form").addEventListener("submit", (e) => { void startCode(e); });
$("back").addEventListener("click", () => { mode = "tab"; void render(); });
$("again").addEventListener("click", () => { mode = "tab"; void render(); });
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
void readTab().then(render);
