"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import type { NflGame, NflGamePage, NflPlay, NflStarter } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { pts, shortName } from "@/lib/format";
import { feedHref, gameState, inGameRows, scoreLine } from "@/lib/nfl";
import { KickoffTime } from "./local-time";
import { PossessionDot } from "./nfl-board";
import { TeamLogo } from "./player-media";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { StatLine } from "./stat-line";
import { useLive } from "./use-live";

export const GAME_POLL_MS = 15_000;
const LATEST = 5;

// The Matchup scorebug turned to clubs: marks at the outer edges, names in Title, the two scores meeting at the
// centre in Display numerals; the status line beneath says where the ball is. `compact` is the feed page's.
export function NflScorebug({ g, compact = false }: { g: NflGame; compact?: boolean }) {
  const on = g.status !== "pre";
  const lead = on ? Math.sign((g.away.score ?? 0) - (g.home.score ?? 0)) : 0;
  const num = compact ? "text-[2rem] sm:text-[2.4rem]" : "text-[2.6rem] sm:text-[3.25rem]";
  const side = (c: NflGame["away"], trailing: boolean, end: "l" | "r") => (
    <div className={`flex min-w-0 items-center gap-2.5 sm:gap-3 ${end === "r" ? "flex-row-reverse text-right" : ""} ${trailing ? "text-foreground/60" : ""}`}>
      <TeamLogo abbr={c.abbr} size={compact ? 32 : 44} fallback />
      <div className="min-w-0">
        <p className={`flex items-center gap-2 font-display font-semibold leading-none tracking-tight ${compact ? "text-lg" : "text-[1.375rem] sm:text-[1.65rem]"} ${end === "r" ? "flex-row-reverse" : ""}`}>
          <span className="truncate">{c.name ?? c.abbr}</span>
          {g.ball === c.abbr && <PossessionDot red={g.red_zone} />}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{c.record ?? c.abbr}</p>
      </div>
    </div>
  );
  return (
    <Card className={BOARD_CARD}>
      <CardContent className="space-y-2 py-(--card-spacing)">
        <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
          {side(g.away, lead < 0, "l")}
          <p className={`font-display font-bold leading-none tabular-nums tracking-tight ${num}`}>
            {on ? <><span className={lead < 0 ? "text-foreground/60" : ""}>{g.away.score}</span><span aria-hidden className="mx-2 text-foreground/30">–</span><span className={lead > 0 ? "text-foreground/60" : ""}>{g.home.score}</span></> : <span className={`${LABEL}`}>vs</span>}
          </p>
          {side(g.home, lead > 0, "r")}
        </div>
        <p className="text-center text-sm text-muted-foreground">
          {g.status === "pre"
            ? <><KickoffTime iso={g.kickoff} />{g.network && <> · {g.network}</>}</>
            : <><span className={g.status === "in" ? "font-medium text-foreground/85" : ""}>{gameState(g)}</span>
                {g.ball && g.down && <> · {g.ball} ball, {g.down}</>}</>}
        </p>
      </CardContent>
    </Card>
  );
}

// One play: the score it made (or the down it was run on) and the clock as the caption, the text as the line.
// My starter's play hangs a lime edge on the left, the opponent's a muted ink edge on the right.
export function NflPlayRow({ p, g }: { p: NflPlay; g: NflGame }) {
  // Both edges are always 3px, so the text column is the same width marked or not; only the marked side is coloured.
  const edge = p.mine.length ? "border-l-start" : p.opp.length ? "border-r-foreground/35" : "";
  const who = [...p.mine, ...p.opp];
  return (
    <div className={`play-row border-x-[3px] border-transparent px-2.5 py-2.5 ${edge}`}>
      <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground tabular-nums">
        {p.scoring && <span className="font-display text-[0.9375rem] font-bold tracking-tight text-foreground">{scoreLine(p, g)}</span>}
        <span>{p.quarter != null && (p.quarter >= 5 ? "OT" : `Q${p.quarter}`)} {p.clock}</span>
        {!p.scoring && p.down && <span>{p.down}</span>}
        {who.length > 0 && <span className="font-medium text-foreground/85">{who.join(", ")}</span>}
      </p>
      <p className="mt-0.5 text-[0.8125rem] leading-snug text-foreground/85">{p.text}</p>
    </div>
  );
}

function StarterHalf({ s, end }: { s?: NflStarter; end: "me" | "opp" }) {
  if (!s) return <span />;
  return (
    <div className={`min-w-0 py-2 ${end === "opp" ? "text-right" : ""}`}>
      <p className={`flex items-baseline gap-2 ${end === "opp" ? "flex-row-reverse" : ""}`}>
        <span className="truncate font-display text-[1.0625rem] font-semibold leading-none tracking-tight">{shortName(s.name)}</span>
        <span className="text-xs text-muted-foreground">{s.slot}</span>
        <span className={`${end === "opp" ? "mr-auto" : "ml-auto"} font-display text-[1.25rem] font-bold leading-none tabular-nums`}>{pts(s.actual)}<span className="ml-1 text-[0.6875rem] font-semibold text-muted-foreground">PTS</span></span>
      </p>
      {s.stat_line && <StatLine text={s.stat_line} className="mt-1 text-xs" wrap />}
    </div>
  );
}

export function NflGameLive({ initial, path }: { initial: NflGamePage; path: string }) {
  const { data } = useLive(initial, path, { pollMs: GAME_POLL_MS });
  const { game: g, week } = data;
  const rows = inGameRows(data.starters);
  const latest = data.scoring.slice(0, LATEST);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link href={week ? `/nfl?week=${week}` : "/nfl"} className={`${LABEL} hover:text-foreground`}>← Week {week}</Link>
        <StaleBadge since={data.stale_since} />
      </div>
      <NflScorebug g={g} />
      {rows.length > 0 && (
        <section className="space-y-2">
          <SectionHeading title="In this game" />
          <Card className={BOARD_CARD}>
            <CardContent className="divide-y divide-foreground/10 py-1">
              {rows.map(([a, b], i) => (
                <div key={a?.player_id ?? b?.player_id ?? i} className="grid grid-cols-2 gap-4"><StarterHalf s={a} end="me" /><StarterHalf s={b} end="opp" /></div>
              ))}
            </CardContent>
          </Card>
        </section>
      )}
      <section className="space-y-2">
        <SectionHeading title="Scoring plays" count={data.scoring.length}>
          <Link href={feedHref(week, g.away.abbr)} className={`${LABEL} -my-2 py-2 text-foreground/55 hover:text-foreground`}>All plays →</Link>
        </SectionHeading>
        <Card className={BOARD_CARD}>
          <CardContent className="divide-y divide-foreground/10 py-1">
            {latest.length === 0
              ? <p className="py-4 text-center text-xs text-muted-foreground">
                  {g.status === "pre" ? <>Kickoff <KickoffTime iso={g.kickoff} />. Scoring plays appear here once the game starts.</> : "No points yet."}
                </p>
              : latest.map((p) => <NflPlayRow key={p.id} p={p} g={g} />)}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
