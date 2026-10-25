"use client";

import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import { ChevronRight, Triangle } from "lucide-react";
import Link from "@/components/league-link";
import { useSyncExternalStore } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { NflBoard, NflClub, NflGame } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { gameHref, gameState, windowLabel } from "@/lib/nfl";
import { KickoffTime } from "./local-time";
import { TeamLogo } from "./player-media";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { useLive } from "./use-live";
import { WeekSelect } from "./week-select";
import { LiveChip } from "./live-chip";
import { Presence, Swap, Ticker, useReorder, whole } from "./motion";

const POLL_MS = 30_000;

const noSubscribe = () => () => {};

// A kickoff window's heading in the viewer's zone. The server has no zone worth using, so it renders a blank
// of the same height and the browser fills in the label once mounted (no hydration mismatch, no stale text).
function WindowLabel({ iso }: { iso: string }) {
  const mounted = useSyncExternalStore(noSubscribe, () => true, () => false);
  return mounted ? <span className="fx-in inline-block">{windowLabel(iso)}</span> : <span aria-hidden className="invisible">&nbsp;</span>;
}

// The club with the ball: the on-field orange the Matchup board's halves use, red inside the 20.
// It fades over to the other club on a change of possession and warms to red crossing the 20.
export function PossessionDot({ red, on = true }: { red: boolean; on?: boolean }) {
  return (
    <Presence show={on}>
    <span className={`size-2 shrink-0 rounded-full transition-colors duration-700 ${red ? "bg-redzone" : "bg-onfield"}`}>
      <span className="sr-only">{red ? "has the ball in the red zone" : "has the ball"}</span>
    </span>
    </Presence>
  );
}

// One club's line of a game card: mark, name and record, the score at the far end. Once the game
// is on the trailing club steps back to 60% ink; a final's winner carries the caret, as on a League tile, centred on
// the row so it sits level with the score.
export function NflClubRow({ c, g, trailing }: { c: NflClub; g: NflGame; trailing: boolean }) {
  const final = g.status === "post";
  return (
    <li className={`flex items-center gap-2.5 py-2.5 transition-colors duration-500 sm:gap-3 ${trailing ? "text-foreground/60" : ""}`}>
      <TeamLogo abbr={c.abbr} size={36} fallback className={`transition-opacity duration-500 ${trailing ? "opacity-70" : ""}`} />
      <div className="min-w-0 flex-1">
        <p className="flex items-center gap-2 font-display text-[1.25rem] font-semibold leading-none tracking-tight">
          <span className="truncate">{c.name ?? c.abbr}</span>
          <PossessionDot red={g.red_zone} on={g.ball === c.abbr} />
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{c.record ?? c.abbr}</p>
      </div>
      <p className="font-display text-[1.5rem] font-bold leading-none tabular-nums tracking-tight">{c.score != null && <Ticker value={c.score} format={whole} gain />}</p>
      {/* the caret's slot is on every row, final or not, so scores line up across cards in every state */}
      <span className="-ml-1 flex w-2.5 shrink-0 items-center self-stretch">
        <Presence show={final && !!c.winner}><span className="flex"><Triangle aria-hidden className="size-2.5 -rotate-90 fill-current" /><span className="sr-only">winner</span></span></Presence>
      </span>
    </li>
  );
}

// One game per card, away over home. The header band says where it stands and, at the far end, whose starters
// play in it; the whole card opens the game.
function GameCard({ g, week }: { g: NflGame; week: number }) {
  const on = g.status !== "pre";
  const lead = on ? Math.sign((g.away.score ?? 0) - (g.home.score ?? 0)) : 0;
  const hint = [g.mine && `${g.mine} mine`, g.opp && `${g.opp} opp`].filter(Boolean).join(" · ");
  return (
    <Link href={gameHref(week, g.away.abbr)} className="group/tile block rounded-lg outline-none">
      <Card className={`${BOARD_CARD} ${g.mine ? "ring-foreground/30" : ""} transition-[box-shadow,background-color] group-hover/tile:bg-foreground/[0.025] group-hover/tile:ring-foreground/35 group-focus-visible/tile:ring-2 group-focus-visible/tile:ring-ring/60`}>
        <CardHeader className="flex flex-row items-center justify-between gap-3 bg-foreground/[0.04] py-2.5 sm:py-3">
          <span className={`${LABEL} flex items-center gap-2`}>
            <LiveChip on={g.status === "in"} />
            <Swap k={g.status}>
              {g.status === "pre"
                ? <span><KickoffTime iso={g.kickoff} />{g.network && <> · {g.network}</>}</span>
                : <span className={g.status === "post" ? "text-foreground" : "text-foreground/85"}>{gameState(g)}</span>}
            </Swap>
          </span>
          <span className={`${LABEL} flex items-center gap-1`}>
            {hint && <Badge variant="muted" shape="chip">{hint}</Badge>}
            <ChevronRight aria-hidden className="-mr-1 size-4 text-foreground/40 transition-[color,translate] group-hover/tile:translate-x-0.5 group-hover/tile:text-foreground/80" />
          </span>
        </CardHeader>
        <CardContent className="-mt-(--card-spacing)">
          <ul className="divide-y divide-foreground/10">
            <NflClubRow c={g.away} g={g} trailing={lead < 0} />
            <NflClubRow c={g.home} g={g} trailing={lead > 0} />
          </ul>
        </CardContent>
      </Card>
    </Link>
  );
}

export function NflBoardLive({ initial, path }: { initial: NflBoard; path: string }) {
  const { data } = useLive(initial, path, { pollMs: POLL_MS });
  const count = data.sections.reduce((n, s) => n + s.games.length, 0);
  // a game moving between windows (kickoff, the whistle) slides from its old section to its new one
  const board = useReorder<HTMLDivElement>(data.sections.map((s) => s.games.map((g) => g.event_id).join()).join("|"));
  return (
    <div ref={board} className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">NFL</h1>
        <StaleBadge since={data.stale_since} />
      </div>
      <SectionHeading title={`Week ${data.week}`} count={count}>
        <LiveChip on={data.live} />
        <WeekSelect week={data.week} current={data.current} weeks={data.weeks} playoffs={data.playoff_weeks} base="/nfl" />
      </SectionHeading>
      {count === 0 && <Empty className="py-6"><EmptyHeader><EmptyDescription>No NFL games in week {data.week}.</EmptyDescription></EmptyHeader></Empty>}
      {data.sections.map((s) => (
        <section key={s.live ? "live" : `${s.final ? "final-" : ""}${s.kickoff ?? "tbd"}`} className="space-y-3">
          <h2 className={`${LABEL} text-foreground/80`}>
            {s.live ? "Live" : <>{s.final && "Final · "}{s.kickoff ? <WindowLabel iso={s.kickoff} /> : "Time TBD"}</>}
          </h2>
          <div className="grid gap-3 md:grid-cols-2 md:gap-4">
            {s.games.map((g) => <div key={g.event_id} data-key={g.event_id}><GameCard g={g} week={data.week} /></div>)}
          </div>
        </section>
      ))}
      {data.byes.length > 0 && <p className={`${LABEL}`}>Bye · {data.byes.join(", ")}</p>}
    </div>
  );
}
