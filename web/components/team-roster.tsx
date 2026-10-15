"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableRow } from "@/components/ui/table";
import type { Player, TeamRoster } from "@/lib/api";
import { BOARD_CARD, HEALTHY, isIdle, LABEL, SLOT_LABEL, STATUS_SHORT, TONE } from "@/lib/board";
import { injuryTone, ordinal, pts, record } from "@/lib/format";
import { gameOn, gamePhase, sideTotals } from "@/lib/game";
import { clubLogoSrc } from "@/lib/teams";
import { ClubLogo } from "./club-logo";
import { GameLink } from "./game-link";
import { GameLine } from "./local-time";
import { PlayerTrigger } from "./player-dialog";
import { ProjectionTrigger, Spread } from "./projection";
import { TeamPanel } from "./player-media";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { StatLine } from "./stat-line";
import { useLive } from "./use-live";
import { gameLinkFor } from "@/lib/nfl";
import { NAME_LINK } from "@/lib/player-log";
import { LiveChip } from "./live-chip";
import { Presence, Swap, Ticker, useReorder } from "./motion";

// Any team's roster as a plain list (tap a team's name anywhere): no calls, no outlooks, just who
// they have and what each is worth this week. Starters in slot order with the slot down the left
// edge like the Matchup board's spine, then bench and IR. Every row is the player's portrait on
// his club's colours, his name (opens the season modal), position and game, and the week's
// number: the projection before kickoff, the points once his game is on.

// The number and its unit, as on the Matchup board: PROJ for the forecast, PTS for a posted score
// with the projection it replaced beneath it, after a live dot while the game is on.
// A tap opens the sources behind the projection; before kickoff their ± stands beside PROJ.
function Num({ p, now, week }: { p: Player; now: number; week: number }) {
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
  return (
    <ProjectionTrigger p={p} week={week} className="ml-auto block text-right tabular-nums"
      label={on ? `${p.name}: ${pts(p.actual)} points, projected ${pts(p.proj)}; show the sources` : undefined}>
      <Swap k={on ? "pts" : "proj"} className={`block font-display text-[1.25rem] font-bold leading-none tracking-tight sm:text-[1.375rem] ${!on && p.proj === 0 ? "text-muted-foreground" : ""}`}>
        <Ticker value={on ? p.actual : p.proj} format={pts} gain={on} />
      </Swap>
      <span className="mt-1 flex items-center justify-end gap-1 whitespace-nowrap text-[0.6875rem] leading-none text-muted-foreground sm:text-xs">
        <Presence show={phase === "live"}><span aria-label="live" className="size-1.5 rounded-full bg-foreground/75" /></Presence>
        <Swap k={on ? "on" : "pending"} className="flex items-center gap-1">
          {on ? <>proj {pts(p.proj)}</> : <><span className="font-display font-semibold uppercase tracking-[0.12em]">proj</span><Spread p={p} /></>}
        </Swap>
      </span>
    </ProjectionTrigger>
  );
}

function Row({ p, slot, week, now }: { p: Player; slot: string | null; week: number; now: number }) {
  const hurt = !HEALTHY.has(p.injury);
  const status = p.injury.replaceAll("_", " ");
  const back = p.return_week != null && p.return_week > week ? p.return_week : null;
  const idle = isIdle(p);
  return (
    <TableRow data-key={p.player_id} className="border-foreground/10 hover:bg-transparent">
      <TableCell className="w-10 pl-3 text-center font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground sm:w-14 sm:pl-4 sm:text-xs">
        {slot}
      </TableCell>
      <TableCell className="py-2 whitespace-normal">
        <div className="flex items-center gap-3">
          <PlayerTrigger p={p} className="block size-12 shrink-0 overflow-hidden rounded-md sm:size-14">
            <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={idle} tile />
          </PlayerTrigger>
          <div className="min-w-0">
            <p className={`flex flex-wrap items-center gap-x-2 gap-y-0.5 font-display text-[1.125rem] font-semibold leading-tight tracking-tight transition-colors duration-500 sm:text-[1.25rem] ${idle ? "text-foreground/70" : ""}`}>
              <PlayerTrigger p={p} className={`min-w-0 rounded-sm ${NAME_LINK}`}>{p.name}</PlayerTrigger>
              <Presence show={hurt}>
                <span title={p.injury_note ?? status} className={`shrink-0 font-bold ${TONE[injuryTone(p.injury)]}`}>
                  <span aria-hidden>{STATUS_SHORT[status] ?? status}</span>
                  <span className="sr-only">{status.toLowerCase()}</span>
                </span>
              </Presence>
              <Presence show={p.bye}><Badge variant="muted" shape="chip">bye</Badge></Presence>
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs leading-4 tabular-nums text-muted-foreground">
              <span className="font-semibold text-foreground/80">{p.pos}</span>
              {/* the bye says itself in the chip; the line keeps only a real game */}
              {!p.bye && <GameLink href={gameLinkFor(p, week)}><GameLine p={p} size={14} /></GameLink>}
              {hurt && back != null && <span className="whitespace-nowrap">back wk {back}</span>}
            </p>
            {p.stat_line && <StatLine text={p.stat_line} className="mt-1 text-xs max-sm:hidden" wrap />}
          </div>
        </div>
      </TableCell>
      <TableCell className="w-20 pr-3 align-middle sm:pr-4">
        <Num p={p} now={now} week={week} />
      </TableCell>
    </TableRow>
  );
}

function Group({ title, players, slots, week, now, total }: { title: string; players: Player[]; slots: boolean; week: number; now: number; total?: React.ReactNode }) {
  // a lineup changed on ESPN: rows slide to their new places
  const body = useReorder<HTMLTableSectionElement>(players.map((p) => p.player_id).join());
  if (players.length === 0) return null;
  return (
    <section className="space-y-3">
      <SectionHeading title={title} count={players.length}>{total}</SectionHeading>
      <Card className={BOARD_CARD}>
        <CardContent className="px-0 sm:px-0">
          <Table aria-label={title}>
            <TableBody ref={body}>
              {players.map((p) => (
                <Row key={p.player_id} p={p} slot={slots ? (SLOT_LABEL[p.slot] ?? p.slot) : null} week={week} now={now} />
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </section>
  );
}

// The starters' line at the end of their heading: the projected total before kickoff; once a
// game is on, the points scored and the estimated final (Matchup's sideTotals).
function StarterTotal({ lineup, projTotal, now }: { lineup: Player[]; projTotal: number; now: number }) {
  const on = lineup.some((p) => gamePhase(p, now) !== "pending");
  const t = sideTotals(lineup, isIdle, now);
  return (
    <span className={`${LABEL} whitespace-nowrap tabular-nums`}>
      <Swap k={on ? "on" : "pending"}>
        {on
          ? <><span className="text-foreground"><Ticker value={t.scored} format={pts} gain /></span> pts <span aria-hidden>·</span> est <span className="text-foreground"><Ticker value={t.estimate} format={pts} /></span></>
          : <><span className="text-foreground"><Ticker value={projTotal} format={pts} /></span> proj</>}
      </Swap>
    </span>
  );
}

export function TeamRosterView({ initial, path }: { initial: TeamRoster; path: string }) {
  const { data, now } = useLive(initial, path);
  const { club, side, week } = data;
  return (
    <div className="space-y-6">
      {/* The mark hangs from the title's cap line rather than centring on the block: a two-line
          name (or the owner line) otherwise leaves it floating between rows. `text-box` trims the
          h1's line box to cap height and baseline so its top edge is the ink's. */}
      <header className="flex items-start gap-4 sm:gap-5">
        <ClubLogo src={clubLogoSrc(club)} abbrev={club.abbrev} size={64} className="sm:hidden" />
        <ClubLogo src={clubLogoSrc(club)} abbrev={club.abbrev} size={80} className="hidden sm:flex" />
        <div className="min-w-0 flex-1">
          <h1 className="text-balance font-display text-[2rem] font-bold leading-[0.95] tracking-tight [text-box:trim-both_cap_alphabetic] sm:text-4xl">
            {club.team}
          </h1>
          {club.owner && <p className="mt-2.5 text-sm text-muted-foreground sm:text-base">{club.owner}</p>}
          <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-display text-base font-semibold tabular-nums text-muted-foreground sm:text-lg">
            <span className="text-foreground">{record(club)}</span>
            {club.rank != null && <><span aria-hidden>·</span><span>{ordinal(club.rank)}</span></>}
            {club.abbrev && <><span aria-hidden>·</span><span className="uppercase tracking-[0.06em]">{club.abbrev}</span></>}
            <span aria-hidden>·</span>
            <span>Week {week}</span>
            {data.mine && <Badge variant="muted" shape="chip">me</Badge>}
            <LiveChip on={gameOn(data.side.lineup)} />
            <StaleBadge since={data.stale_since} />
          </p>
        </div>
      </header>
      <Group title="Starters" players={side.lineup} slots week={week} now={now}
        total={<StarterTotal lineup={side.lineup} projTotal={side.proj_total} now={now} />} />
      <Group title="Bench" players={side.bench} slots={false} week={week} now={now} />
      <Group title="IR" players={side.ir} slots={false} week={week} now={now} />
    </div>
  );
}
