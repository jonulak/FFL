"use client";

import type { Roster } from "@/lib/api";
import { RosterCards } from "./roster-cards";
import { useLive } from "./use-live";

// Only the cards go live: the page header (outlook run, stale badge) is server state.
export function RosterLive({ initial }: { initial: Roster }) {
  const { data } = useLive(initial, "/roster");
  return <RosterCards cards={data.cards} week={data.week} runBusy={data.outlook_run.running} />;
}
