"use client";

import type { Plays } from "@/lib/api";
import { boardHref } from "@/lib/matchup-link";
import { PlayFeedCard } from "./play-feed";
import { useLive } from "./use-live";

// One game's whole week: mine at `/plays`, any other at `/plays?team=` (`path`, the GET the live
// ticks refetch); the card's heading line leads back to that game's board.
export function PlaysLive({ initial, path }: { initial: Plays; path: string }) {
  const { data } = useLive(initial, path);
  return (
    <PlayFeedCard
      plays={data.plays}
      title="All plays"
      count={data.plays.length}
      link={{ href: boardHref(data.mine, data.week, data.me?.team_id ?? null), label: "Matchup" }}
      empty="No plays yet this week. A row appears each time a starter in the matchup scores."
    />
  );
}
