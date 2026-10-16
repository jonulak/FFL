"use client";

import type { Plays } from "@/lib/api";
import { boardHref } from "@/lib/matchup-link";
import { PlayFeedCard } from "./play-feed";
import { useLive } from "./use-live";
import { LiveChip } from "./live-chip";

// One game's whole week: mine at `/plays`, any other at `/plays?team=` (`path`, the GET the live
// ticks refetch); the card's heading line leads back to that game's board. The heading rides the
// ticks too, so the live badge comes and goes with this game's starters, not the whole slate.
export function PlaysLive({ initial, path }: { initial: Plays; path: string }) {
  const { data } = useLive(initial, path);
  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
            Plays
            {data.week != null && <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>}
          </h1>
          <LiveChip on={data.game_on} />
        </div>
        {/* another game's plays say whose they are; my own page needs no caption */}
        {!data.mine && data.me && (
          <p className="font-display text-lg font-semibold leading-tight tracking-tight text-foreground/80">
            {data.me.team} <span className="font-medium uppercase tracking-[0.14em] text-muted-foreground text-sm">vs</span> {data.opp?.team ?? "bye"}
          </p>
        )}
      </div>
      <PlayFeedCard
        plays={data.plays}
        title="All plays"
        count={data.plays.length}
        link={{ href: boardHref(data.mine, data.week, data.me?.team_id ?? null, data.current), label: "Matchup" }}
        empty="No plays yet this week. A row appears each time a starter in the matchup scores."
      />
    </div>
  );
}
