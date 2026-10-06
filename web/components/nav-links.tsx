"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

const LINKS = [["/", "Matchup"], ["/roster", "Roster"], ["/league", "League"], ["/transactions", "Transactions"], ["/players", "Players"]] as const;

// The page tabs, set like the chyron of a broadcast board: condensed caps, letterspaced, the
// current page in full ink with a bar under it, the rest stepped back. On a phone the strip
// scrolls and fades at its right edge; the current tab is brought into view on arrival.
export function NavLinks() {
  const path = usePathname();
  const current = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    // centred, not nearest: nearest parks the tab under the strip's fade at the right edge
    current.current?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [path]);
  return (
    <nav
      aria-label="Pages"
      className="-mx-1 flex min-w-0 items-stretch overflow-x-auto px-1 [scrollbar-width:none] [mask-image:linear-gradient(to_right,black_calc(100%-1.75rem),transparent)] sm:[mask-image:none]"
    >
      {LINKS.map(([href, label]) => {
        const active = href === "/" ? path === "/" : path.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            ref={active ? current : undefined}
            aria-current={active ? "page" : undefined}
            className="relative flex items-center px-2.5 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] whitespace-nowrap text-foreground/55 transition-colors hover:text-foreground/85 aria-[current=page]:text-foreground after:absolute after:inset-x-2.5 after:bottom-0 after:h-0.5 after:bg-foreground after:opacity-0 aria-[current=page]:after:opacity-100"
          >
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
