"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDecisionPrefs } from "./decision-prefs";

const LINKS = [["/", "Matchup"], ["/roster", "Roster"], ["/league", "League"], ["/nfl", "NFL"], ["/transactions", "Transactions"], ["/players", "Players"], ["/decisions", "Decisions"]] as const;

// A modified or middle click opens the page in a new tab and leaves this one where it is, so it lights nothing.
// Keyboard activation arrives as a plain click (button 0) and still lights the tab at once.
const newTab = (e: Event) => e instanceof MouseEvent && (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0);

const activeOf =(path: string) => LINKS.find(([href]) => (href === "/" ? path === "/" : path.startsWith(href)))?.[0] ?? null;

// The page tabs, set like the chyron of a broadcast board: shadcn Tabs in the line variant, each tab a Link
// (pos-tabs' pattern). The strip lives in the layout, so a page change keeps it mounted and Base UI's
// indicator slides from the tab left to the one arrived at. On a phone the strip scrolls and fades at its
// right edge; the current tab is brought into view on arrival.
export function NavLinks() {
  const path = usePathname();
  const active = activeOf(path);
  const [tapped, setTapped] = useState<string | null>(null);
  const [seen, setSeen] = useState(active);
  if (seen !== active) {
    setSeen(active);
    setTapped(null);
  }
  const current = useRef<HTMLAnchorElement>(null);
  const { page } = useDecisionPrefs(); // the Decisions tab goes with the page (Settings)
  useEffect(() => {
    // centred, not nearest: nearest parks the tab under the strip's fade at the right edge
    current.current?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [path]);
  return (
    <nav aria-label="Pages" className="flex min-w-0 items-stretch">
      {/* the list is the scroller, so the 4px it reaches past the nav on each side is its own padding */}
      <Tabs value={tapped ?? active} onValueChange={(v, { event }) => { if (!newTab(event)) setTapped(v as string); }} className="-mx-1 min-w-0 flex-1">
        <TabsList variant="line" indicatorInset="0.625rem"
          className="h-full w-full group-data-horizontal/tabs:h-full justify-start gap-0 overflow-x-auto overflow-y-hidden px-1 py-0 [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_calc(100%-1.75rem),transparent)] sm:[mask-image:none]">
          {LINKS.filter(([href]) => page || href !== "/decisions").map(([href, label]) => (
            <TabsTrigger
              key={href}
              value={href}
              nativeButton={false}
              render={<Link href={href} ref={href === active ? current : undefined} aria-current={href === active ? "page" : undefined} />}
              className="h-full flex-none rounded-none border-0 px-2.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:text-foreground/85 data-active:text-foreground dark:text-foreground/55 dark:hover:text-foreground/85 dark:data-active:text-foreground"
            >
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
    </nav>
  );
}
