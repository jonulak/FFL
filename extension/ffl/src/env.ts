// What every script shares: the browser's extension API (Safari's `browser`, Chrome's `chrome`; both answer promises
// in MV3), FFL's address (fixed at build: build.mjs --origin), and the messages between the scripts.
import type { Partial2 } from "./pure";

declare const __FFL_ORIGIN__: string;
declare const __FFL_SITE__: string;
export const FFL_ORIGIN: string = __FFL_ORIGIN__;
// FFL's site as a match pattern (the manifest's): no port, which Safari won't take (build.mjs)
export const FFL_SITE: string = __FFL_SITE__;

export const api: typeof chrome = (globalThis as unknown as { browser?: typeof chrome }).browser ?? chrome;

export type Msg =
  | { kind: "page"; cookies?: Partial2 }      // content → background: an ESPN page loaded, or its login changed
  | { kind: "start" } | { kind: "poll" } | { kind: "cancel" } | { kind: "unpair" }
  | { kind: "state" } | { kind: "link" } | { kind: "linked" }   // the FFL site's content script (content.ts)
  | { kind: "send"; cookies?: Partial2 }      // popup → background: Send now (with the active tab's cookies, if any)
  | { kind: "read" };                         // popup → content: this page's cookies
