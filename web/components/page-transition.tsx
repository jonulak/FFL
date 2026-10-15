"use client";

import { usePathname } from "next/navigation";

// A page change on the board: the new page rises into place (globals.css, "Page change"). Keyed on
// the path, so only a navigation to another page plays it; a refresh, a live tick or a query change
// on the same page never does (those animate their own content, so a search box keeps its focus).
// The nav stays put: it is outside.
// A CSS animation, not a <ViewTransition>: a view transition snapshots the whole page, old and new,
// and Transactions or Players stand ~17,500px tall on a phone, ~250MB a snapshot at 3x. Flipping
// pages fast stacked them past 2GB and iOS killed the app (ffl-ix7).
export function PageTransition({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return <div key={path} className="page-in">{children}</div>;
}
