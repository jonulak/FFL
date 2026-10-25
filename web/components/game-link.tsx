"use client";

import Link from "next/link";
import { cn } from "cn";
import { useOpenLeague } from "./league-context";

// A game line made a way into that NFL game. The rows it sits in carry their own tap target (the Matchup
// breakdown is an overlay button, the Roster expand a chevron), so the link stands above it (relative z-10).
// Its tap target is exactly the faint chip it draws, never an invisible pad: a pad reaching up over the
// player's name made a tap on the name open the game. The chip's padding is cancelled by negative margins
// so the line does not move; a call site with room around the line (Matchup) passes a taller one. The chip is
// the cue, so ItemDescription's own link underline and hover colour are overridden (`!`, its `[&>a]` wins otherwise).
// Another league's line is no link: the game page marks home's lineups (league-context.tsx).
export function GameLink({ href, children, className = "" }: { href: string | null; children: React.ReactNode; className?: string }) {
  const { home } = useOpenLeague();
  if (!href || !home) return <>{children}</>;
  return (
    <Link href={href}
      className={cn("relative z-10 -mx-1 -my-0.5 inline-flex min-w-0 items-center rounded-sm bg-foreground/[0.05] px-1 py-0.5 no-underline! outline-none hover:text-inherit! transition-colors hover:bg-foreground/[0.1] focus-visible:ring-2 focus-visible:ring-ring/60", className)}>
      {children}
    </Link>
  );
}
