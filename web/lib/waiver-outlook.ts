import type { PlayerOutlook } from "./api";

// What the Players outlook dialog shows.
export type OutlookPhase = "loading" | "empty" | "ready";

export function outlookPhase(o: PlayerOutlook | null): OutlookPhase {
  if (!o) return "loading";
  return o.bullets?.length ? "ready" : "empty";
}
