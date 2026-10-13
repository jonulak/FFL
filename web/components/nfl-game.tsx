"use client";

import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { NflGame, NflGamePage, NflPlay, NflStarter } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { pts, shortName } from "@/lib/format";
import { gameState, inGameRows, scoreLine, type FeedFilter } from "@/lib/nfl";
import { KickoffTime } from "./local-time";
import { NflPlayFeed } from "./nfl-feed";
import { PossessionDot } from "./nfl-board";
import { TeamLogo } from "./player-media";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { StatLine } from "./stat-line";
import { useLive } from "./use-live";
import { Presence, Swap, Ticker, whole } from "./motion";

export const GAME_POLL_MS = 15_000;

// The Matchup scorebug turned to clubs: marks at the outer edges, names in Title, the two scores meeting at the
// centre in Display numerals; the status line beneath says where the ball is.
export function NflScorebug({ g }: { g: NflGame }) {
  const on = g.status !== "pre";
  const lead = on ? Math.sign((g.away.score ?? 0) - (g.home.score ?? 0)) : 0;
  const num = "text-[2.6rem] sm:text-[3.25rem]";
  const side = (c: NflGame["away"], trailing: boolean, end: "l" | "r") => (
    <div className={`flex min-w-0 items-center gap-2.5 transition-colors duration-500 sm:gap-3 ${end === "r" ? "flex-row-reverse text-right" : ""} ${trailing ? "text-foreground/60" : ""}`}>
      <TeamLogo abbr={c.abbr} size={44} fallback />
      <div className="min-w-0">
        <p className={`flex items-center gap-2 font-display font-semibold leading-none tracking-tight text-[1.375rem] sm:text-[1.65rem] ${end === "r" ? "flex-row-reverse" : ""}`}>
          <span className="truncate sm:hidden">{c.abbr}</span><span className="hidden truncate sm:inline">{c.name ?? c.abbr}</span>
          <PossessionDot red={g.red_zone} on={g.ball === c.abbr} />
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
            <Swap k={on ? "on" : "pre"}>
              {on ? <><span className={`transition-colors duration-500 ${lead < 0 ? "text-foreground/60" : ""}`}><Ticker value={g.away.score ?? 0} format={whole} gain /></span><span aria-hidden className="mx-2 text-foreground/30">–</span><span className={`transition-colors duration-500 ${lead > 0 ? "text-foreground/60" : ""}`}><Ticker value={g.home.score ?? 0} format={whole} gain /></span></> : <span className={`${LABEL}`}>vs</span>}
            </Swap>
          </p>
          {side(g.home, lead > 0, "r")}
        </div>
        <p className="text-center text-sm text-muted-foreground">
          <Swap k={g.status}>
            {g.status === "pre"
              ? <><KickoffTime iso={g.kickoff} />{g.network && <> · {g.network}</>}</>
              : <><span className={g.status === "in" ? "font-medium text-foreground/85" : ""}>{gameState(g)}</span>
                  {g.ball && g.down && <> · <Swap>{`${g.ball} ball, ${g.down}`}</Swap></>}</>}
          </Swap>
        </p>
      </CardContent>
    </Card>
  );
}

// One play: the score it made (or the down it was run on) and the clock as the caption, the text as the line.
// A marked play hangs one 3px rail on its left: lime for my starter's, ink at 60% for the opponent's. The left
// edge is always 3px, so the text column is the same width marked or not. A turnover or a penalty is a chip on
// the caption line, inside the row so it slides in with it.
export function NflPlayRow({ p, g, prev }: { p: NflPlay; g: NflGame; prev?: NflPlay["score"] | null }) {
  const rail = p.mine.length ? "border-l-start" : p.opp.length ? "border-l-foreground/60" : "";
  const who = [...p.mine, ...p.opp];
  const sc = p.scoring ? scoreLine(p, g, prev) : null;
  const part = (side: "away" | "home", text: string) =>
    <span className={sc?.scorer === side ? "text-foreground" : "font-semibold text-muted-foreground"}>{text}</span>;
  return (
    <div className={`play-row border-l-[3px] border-transparent py-2.5 pl-2.5 ${rail}`}>
      <p className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground tabular-nums">
        {sc && (
          <span className="font-display text-[0.9375rem] font-bold tracking-tight">
            {part("away", sc.away)}<span aria-hidden className="mx-1 text-muted-foreground">·</span>{part("home", sc.home)}
          </span>
        )}
        <span>{p.quarter != null && (p.quarter >= 5 ? "OT" : `Q${p.quarter}`)} {p.clock}</span>
        {!p.scoring && p.down && <span>{p.down}</span>}
        {who.length > 0 && <span className="font-medium text-foreground/85">{who.join(", ")}</span>}
        {p.turnover && <Badge variant="close" shape="chip" className="self-center">turnover</Badge>}
        {p.penalty && <Badge variant="muted" shape="chip" className="self-center">flag</Badge>}
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
        <span className={`${end === "opp" ? "mr-auto" : "ml-auto"} font-display text-[1.25rem] font-bold leading-none tabular-nums`}><Ticker value={s.actual} format={pts} gain /><span className="ml-1 text-[0.6875rem] font-semibold text-muted-foreground">PTS</span></span>
      </p>
      <p className="mt-1 text-xs text-muted-foreground">{s.slot}{s.team && ` · ${s.team}`}</p>
      {s.stat_line && <StatLine text={s.stat_line} className="mt-1 text-xs" wrap />}
    </div>
  );
}

export function NflGameLive({ initial, path, filter, club }: { initial: NflGamePage; path: string; filter: FeedFilter; club: string }) {
  const { data } = useLive(initial, path, { pollMs: GAME_POLL_MS });
  const { game: g, week } = data;
  const rows = inGameRows(data.starters);
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Link href={week ? `/nfl?week=${week}` : "/nfl"} className={`${LABEL} transition-colors hover:text-foreground`}>← Week {week}</Link>
        <StaleBadge since={data.stale_since} />
      </div>
      <NflScorebug g={g} />
      {/* opens at the first kickoff with a starter in it */}
      <Presence show={rows.length > 0} fold>
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
      </Presence>
      <NflPlayFeed data={data} filter={filter} club={club} />
    </div>
  );
}
