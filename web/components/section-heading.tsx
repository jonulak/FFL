"use client";

import { Separator } from "@/components/ui/separator";
import { Ticker, whole } from "./motion";


// The section heading the Roster page uses: condensed caps, a muted count, a hairline running
// out; `children` sit at the far end of the line (the scoreboard's week picker).
export function SectionHeading({ title, count, children }: { title: string; count?: number; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <h2 className="font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</h2>
      {count != null && <span className="font-display text-base font-medium tabular-nums text-muted-foreground"><Ticker value={count} format={whole} /></span>}
      <Separator className="flex-1 bg-foreground/12" />
      {children}
    </div>
  );
}
