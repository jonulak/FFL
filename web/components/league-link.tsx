"use client";

import NextLink from "next/link";
import { useRouter as useNextRouter } from "next/navigation";
import { useMemo, type ComponentProps } from "react";
import { leagueHref } from "@/lib/league";
import { useOpenLeague } from "./league-context";

// next/link and next/navigation's router, kept in the open league (ffl-soc.4.9): every in-app link and router move
// made on another league's page carries its ?league=, so a tap never drops the reader into home (lib/league.ts
// leagueHref). Home's links are exactly as before. Import these in place of Next's; sw-register.tsx keeps Next's own
// router, since a push names the league it opens.
export default function Link({ href, ...props }: ComponentProps<typeof NextLink>) {
  const { key } = useOpenLeague();
  return <NextLink href={typeof href === "string" ? leagueHref(href, key) : href} {...props} />;
}

export function useRouter(): ReturnType<typeof useNextRouter> {
  const router = useNextRouter();
  const { key } = useOpenLeague();
  return useMemo(() => ({
    ...router,
    push: (href, options) => router.push(leagueHref(href, key), options),
    replace: (href, options) => router.replace(leagueHref(href, key), options),
    prefetch: (href, options) => router.prefetch(leagueHref(href, key), options),
  }), [router, key]);
}
