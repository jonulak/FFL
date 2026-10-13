"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, ViewTransition } from "react";
import { useDecisionPrefs } from "./decision-prefs";

const LINKS = [["/", "Matchup"], ["/roster", "Roster"], ["/league", "League"], ["/nfl", "NFL"], ["/transactions", "Transactions"], ["/players", "Players"], ["/decisions", "Decisions"]] as const;

// The page tabs, set like the chyron of a broadcast board: condensed caps, letterspaced, the
// current page in full ink with a bar under it, the rest stepped back. On a phone the strip
// scrolls and fades at its right edge; the current tab is brought into view on arrival.
export function NavLinks() {
  const path = usePathname();
  const current = useRef<HTMLAnchorElement>(null);
  const { page } = useDecisionPrefs(); // the Decisions tab goes with the page (Settings)
  useEffect(() => {
    // centred, not nearest: nearest parks the tab under the strip's fade at the right edge
    // at once, not smooth: the page change has already captured where the tab bar lands
    current.current?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [path]);
  return (
    <nav
      aria-label="Pages"
      className="-mx-1 flex min-w-0 items-stretch overflow-x-auto px-1 [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_calc(100%-1.75rem),transparent)] sm:[mask-image:none]"
    >
      {LINKS.filter(([href]) => page || href !== "/decisions").map(([href, label]) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            ref={active ? current : undefined}
            aria-current={active ? "page" : undefined}
            className="relative flex items-center px-2.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] whitespace-nowrap text-foreground/55 transition-colors duration-300 hover:text-foreground/85 aria-[current=page]:text-foreground"
          >
            {label}
            {/* one bar, named for the page change: it slides from the tab left to the one arrived at */}
            {active && (
              <ViewTransition name="nav-tab" share="nav-tab" default="none">
                <span aria-hidden className="absolute inset-x-2.5 bottom-0 h-0.5 bg-foreground" />
              </ViewTransition>
            )}
          </Link>
        );
      })}
    </nav>
  );
}
