"use client";

import type { Week } from "@/lib/api";
import { MatchupView } from "./matchup";
import { useLive } from "./use-live";

// `path` is the GET the live ticks refetch: my week by default, `?team=` for a League game,
// which also carries `back`, the scoreboard it was opened from.
export function MatchupLive({ initial, path, back }: { initial: Week; path?: string; back?: string }) {
  const { data, now } = useLive(initial, path ?? `/week/${initial.week}`);
  return <MatchupView data={data} now={now} back={back} />;
}
