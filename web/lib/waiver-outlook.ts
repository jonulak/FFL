import type { PlayerOutlook } from "./api";

// What the Players outlook dialog shows. Stored bullets win over a failure (the failure is then a
// note under them, the old bullets still good).
export type OutlookPhase = "loading" | "empty" | "ready" | "failed";

export function outlookPhase(o: PlayerOutlook | null): OutlookPhase {
  if (!o) return "loading";
  if (o.bullets?.length) return "ready";
  return o.error ? "failed" : "empty";
}
