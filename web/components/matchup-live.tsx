"use client";

import type { Play, Week } from "@/lib/api";
import { type LiveDelta, overlayWeek } from "@/lib/overlay";
import { MatchupView } from "./matchup";
import { useLive } from "./use-live";

// The board's plays strip: a tick that moved one of its players asks /plays for the newest few (as many as the
// board shows), since the feed rows are written beside the points, not sent with them.
const sidePath = (d: Week, delta: LiveDelta): string | null => {
  const ids = [d.me, d.opp].flatMap((s) => (s ? [...s.lineup, ...s.bench, ...s.ir] : [])).map((p) => String(p.player_id));
  if (!ids.some((id) => delta.points && id in delta.points)) return null;
  return `/plays?week=${d.week}${d.mine ? "" : `&team=${d.me.team_id}`}&limit=${Math.max(d.plays.length, 5)}`;
};
const side = { path: sidePath, put: (d: Week, got: unknown) => ({ ...d, plays: (got as { plays: Play[] }).plays }) };

// `path` is the GET the live ticks refetch: my week by default, `?team=` for a League game,
// which also carries `back`, the scoreboard it was opened from.
export function MatchupLive({ initial, path, back }: { initial: Week; path?: string; back?: string }) {
  const { data, now } = useLive(initial, path ?? `/week/${initial.week}`, { overlay: overlayWeek, side });
  return <MatchupView data={data} now={now} back={back} />;
}
