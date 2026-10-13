"use client";

import Link from "next/link";
import { useState } from "react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const POS = ["QB", "RB", "WR", "TE", "D/ST", "K"];

// shadcn Tabs in the line variant, each tab rendered as a Link: the position is server state in
// `?pos=`, so a tab is a navigation the page re-renders from, and the list is only the chyron
// strip (condensed caps, the current position in full ink over a bar) — there are no panels.
// From `sm` the hairline under it belongs to the row it shares with the Players search.
// `active` is null while a search spans every position: no tab is lit. `extra` rides on every
// tab's link (the Free agents toggle's "&fa=0"), so a tab tap keeps the view it was in.
// The tab tapped lights at once and the bar slides to it while the page is fetched; the server's
// answer takes over when it lands (state off a prop, set in render).
export function PosTabs({ active, base, extra = "" }: { active: string | null; base: string; extra?: string }) {
  const [tapped, setTapped] = useState<string | null>(null);
  const [seen, setSeen] = useState(active);
  if (seen !== active) {
    setSeen(active);
    setTapped(null);
  }
  return (
    <Tabs value={tapped ?? active} onValueChange={(v) => setTapped(v as string)} aria-label="Position">
      <TabsList variant="line" indicatorInset="0.625rem" className="h-10 w-full justify-start gap-0 overflow-x-auto overflow-y-hidden border-foreground/12 p-0 [scrollbar-width:none] max-sm:border-b">
        {POS.map((p) => (
          <TabsTrigger
            key={p}
            value={p}
            nativeButton={false}
            render={<Link href={`${base}?pos=${encodeURIComponent(p)}${extra}`} />}
            className="h-full flex-none rounded-none px-2.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground/85 data-active:text-foreground dark:text-foreground/55 dark:hover:text-foreground/85 dark:data-active:text-foreground"
          >
            {p}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
