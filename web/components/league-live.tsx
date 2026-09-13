"use client";

import type { League } from "@/lib/api";
import { LeagueView } from "./league";
import { useLive } from "./use-live";

// The scoreboard for the current week moves with the live feed; a past or future week has
// live=false and stays as the server rendered it.
export function LeagueLive({ initial, path }: { initial: League; path: string }) {
  const { data } = useLive(initial, path);
  return <LeagueView data={data} />;
}
