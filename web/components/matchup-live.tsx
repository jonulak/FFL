"use client";

import type { Week } from "@/lib/api";
import { MatchupView } from "./matchup";
import { useLive } from "./use-live";

export function MatchupLive({ initial }: { initial: Week }) {
  const { data, now } = useLive(initial, `/week/${initial.week}`);
  return <MatchupView data={data} now={now} />;
}
