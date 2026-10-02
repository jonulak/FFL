import type { WaiverOutlook } from "./api";

// What the Waivers outlook dialog shows. A running write wins over whatever is stored; stored
// bullets win over a failure (the failure is then a note under them, the old bullets still good).
export type OutlookPhase = "loading" | "empty" | "writing" | "ready" | "failed";

export function outlookPhase(o: WaiverOutlook | null): OutlookPhase {
  if (!o) return "loading";
  if (o.running) return "writing";
  if (o.bullets?.length) return "ready";
  return o.error ? "failed" : "empty";
}
