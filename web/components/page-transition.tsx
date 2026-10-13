"use client";

import { usePathname } from "next/navigation";
import { ViewTransition } from "react";

// A page change on the board: the page it leaves fades out quickly and the new one rises into
// place (globals.css, "Page change"). Keyed on the path, so only a navigation to another page
// plays it; a refresh, a live tick or a query change on the same page never does (those animate
// their own content, so a search box keeps its focus). The nav stays put: it is outside.
export function PageTransition({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <ViewTransition key={path} enter="page-in" exit="page-out" default="none">
      <div>{children}</div>
    </ViewTransition>
  );
}
