"use client";

import { Badge } from "@/components/ui/badge";
import { Presence } from "./motion";

// The LIVE chip: muted, not a verdict colour, its ink dot breathing while games are on. It fades in
// at the first kickoff and out at the last whistle rather than blinking on and off.
export function LiveChip({ on = true }: { on?: boolean }) {
  return (
    <Presence show={on}>
      <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75 motion-safe:animate-pulse" />live</Badge>
    </Presence>
  );
}
