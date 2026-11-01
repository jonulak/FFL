// What every script shares: the browser's extension API (Safari's `browser`, Chrome's `chrome`; both answer promises
// in MV3) and the messages between the scripts. FFL's address isn't built in: the popup learns it from the open tab (or
// the typed fallback) and the background keeps it in storage.local `server`.
import type { Partial2 } from "./pure";

export const api: typeof chrome = (globalThis as unknown as { browser?: typeof chrome }).browser ?? chrome;

export type Msg =
  | { kind: "page"; cookies?: Partial2 }      // content → background: an ESPN page loaded, or its login changed
  | { kind: "connect-tab"; origin: string }   // popup → background: pair with the FFL open in the tab, its session confirming
  | { kind: "pair-code"; server: string }     // popup → background: a pairing whose code the user types in FFL
  | { kind: "start" } | { kind: "poll" } | { kind: "cancel" } | { kind: "unpair" }
  | { kind: "send"; cookies?: Partial2 }      // popup → background: Send now (with the active tab's cookies, if any)
  | { kind: "read" };                         // popup → content: this page's cookies
