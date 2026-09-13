"use client";

import type { Plays } from "@/lib/api";
import { PlayFeedCard } from "./play-feed";
import { useLive } from "./use-live";

export function PlaysLive({ initial }: { initial: Plays }) {
  const { data } = useLive(initial, "/plays");
  return (
    <PlayFeedCard
      plays={data.plays}
      title="All plays"
      count={data.plays.length}
      link={{ href: "/", label: "Matchup" }}
      empty="No plays yet this week. A row appears each time a starter in the matchup scores."
    />
  );
}
