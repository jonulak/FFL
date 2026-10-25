import { ChevronRight, Triangle } from "lucide-react";
import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { League, ScoreSide } from "@/lib/api";
import { BOARD_CARD, isIdle, LABEL } from "@/lib/board";
import { pts, record } from "@/lib/format";
import { sideTotals } from "@/lib/game";
import { matchupHref } from "@/lib/matchup-link";
import { clubLogoSrc } from "@/lib/teams";
import { finalP } from "@/lib/win-prob";
import { ClubLogo } from "./club-logo";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { TeamLink } from "./team-link";
import { WeekSelect } from "./week-select";
import { OddsHalf, TileOdds } from "./win-meter";
import { LiveChip } from "./live-chip";
import { Presence, Swap, Ticker, useReorder } from "./motion";
import { ViewTransition } from "react";

// One full-width table: rank, mark and name, then the record and the points columns in
// condensed numerals. A heavier rule under the last playoff seed marks the cut line.
function Standings({ rows, playoffTeams }: { rows: League["standings"]; playoffTeams: number }) {
  // a week's results move the table: each club slides to its new rank
  const body = useReorder<HTMLTableSectionElement>(rows.map((t) => t.team_id).join());
  return (
    <Card className={BOARD_CARD}>
      <CardContent className="px-0 sm:px-0">
        <Table>
          <TableHeader className="bg-foreground/[0.04]">
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className={`${LABEL} w-10 pl-3 text-right sm:pl-4`}>#</TableHead>
              <TableHead className={LABEL}>Team</TableHead>
              <TableHead className={`${LABEL} text-right`}>W-L</TableHead>
              <TableHead className={`${LABEL} pr-3 text-right sm:pr-2`}>PF</TableHead>
              <TableHead className={`${LABEL} hidden text-right sm:table-cell`}>PA</TableHead>
              <TableHead className={`${LABEL} hidden pr-3 text-right sm:table-cell sm:pr-4`}>Strk</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody ref={body}>
            {rows.map((t) => (
              <TableRow
                key={t.team_id}
                data-key={t.team_id}
                className={`hover:bg-transparent ${t.me ? "bg-foreground/[0.05]" : ""} ${t.rank === playoffTeams && t.rank < rows.length ? "border-b-2 border-foreground/35" : "border-foreground/10"}`}
              >
                <TableCell className="pl-3 text-right font-display text-lg font-semibold tabular-nums text-muted-foreground sm:pl-4">{t.rank}</TableCell>
                <TableCell className="w-full max-w-0 py-2 whitespace-normal">
                  <div className="flex items-center gap-2.5 sm:gap-3">
                    <ClubLogo src={clubLogoSrc(t)} abbrev={t.abbrev} size={32} />
                    <div className="min-w-0">
                      <p className="flex min-w-0 items-center gap-x-2 font-display text-[1.25rem] font-semibold leading-none tracking-tight">
                        <TeamLink id={t.team_id} className="truncate">{t.team}</TeamLink>
                        {t.me && <Badge variant="muted" shape="chip">me</Badge>}
                      </p>
                      {/* the manager under the club; the abbreviation stands in only when ESPN names no one
                          (the mark already carries it when there is no logo) */}
                      <p className="mt-1 flex min-w-0 text-xs text-muted-foreground">
                        <span className="truncate">{t.owner || t.abbrev}</span>
                        {t.streak && <span className="shrink-0 whitespace-pre sm:hidden"> · {t.streak}</span>}
                      </p>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-right font-display text-[1.25rem] font-bold tabular-nums tracking-tight"><Swap>{record(t)}</Swap></TableCell>
                <TableCell className="pr-3 text-right font-display text-lg font-semibold tabular-nums tracking-tight sm:pr-2"><Ticker value={t.points_for} format={pts} /></TableCell>
                <TableCell className="hidden text-right font-display text-lg font-medium tabular-nums tracking-tight text-foreground/70 sm:table-cell"><Ticker value={t.points_against} format={pts} /></TableCell>
                <TableCell className="hidden pr-3 text-right font-display text-lg font-medium tabular-nums text-foreground/70 sm:table-cell sm:pr-4"><Swap>{t.streak ?? ""}</Swap></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

// One side of a scorebug row: mark, name and record left, the number right. Once points post
// the trailing side steps back to 60% ink so the leader reads first. In a final game the winner's
// score carries a solid caret pointing at it, the way a broadcast scoreboard closes a result; the
// loser, and every row of a game not yet final, keeps the caret's column empty so scores align across tiles.
// `odds` (the away side's) and `half` draw this row's half of the card's win meter at the edge.
function ScoreRow({ side, on, final, now, trailing, won, odds, half }: { side: ScoreSide; on: boolean; final: boolean; now: number; trailing: boolean; won: boolean; odds: number | null; half: "top" | "bottom" }) {
  // under a live score, the estimated final as the Matchup board sums it; ESPN's projection for a side
  // with no lineup to sum, and nothing once the game is final (the score is the final)
  const est = side.starters.length > 0 ? sideTotals(side.starters, isIdle, now).estimate : null;
  return (
    <li className={`relative flex items-center gap-2.5 py-2.5 transition-colors duration-500 sm:gap-3 ${trailing ? "text-foreground/60" : ""}`}>
      {odds != null && <OddsHalf p={odds} half={half} />}
      <ClubLogo src={clubLogoSrc(side)} abbrev={side.abbrev} size={36} className={`transition-opacity duration-500 ${trailing ? "opacity-70" : ""}`} />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 items-center gap-x-2 font-display text-[1.25rem] font-semibold leading-none tracking-tight">
          <span className="truncate">{side.team}</span>
          {side.me && <Badge variant="muted" shape="chip">me</Badge>}
        </p>
        {/* the manager, then the record; a long name gives way, the record never does */}
        <p className="mt-1 flex min-w-0 text-xs text-muted-foreground">
          {side.owner && <><span className="truncate">{side.owner}</span><span aria-hidden className="shrink-0 whitespace-pre"> · </span></>}
          <span className="shrink-0 tabular-nums">{record(side)}</span>
        </p>
      </div>
      <div className="text-right tabular-nums">
        {/* a week beyond the current one has pairings but no projection yet */}
        <p className={`font-display text-[1.5rem] font-bold leading-none tracking-tight ${!on && side.proj === 0 ? "text-muted-foreground" : ""}`}>
          <Swap k={on ? "pts" : side.proj > 0 ? "proj" : "none"}>
            {on ? <Ticker value={side.score} format={pts} gain /> : side.proj > 0 ? <Ticker value={side.proj} format={pts} /> : "—"}
          </Swap>
        </p>
        <Presence show={on && !final && side.proj > 0}>
          <p className="mt-0.5 text-xs text-muted-foreground">{est != null ? <>est <Ticker value={est} format={pts} /></> : `proj ${pts(side.proj)}`}</p>
        </Presence>
      </div>
      {/* the caret's slot is on every row, final or not, so scores line up across tiles in every state */}
      <span className="-ml-1 flex w-2.5 shrink-0 self-start pt-[0.4375rem] sm:-ml-1.5">
        <Presence show={won}><span className="flex"><Triangle aria-hidden className="size-2.5 -rotate-90 fill-current" /><span className="sr-only">winner</span></span></Presence>
      </span>
    </li>
  );
}

// One matchup per card, away over home the way a broadcast stacks a scorebug. Before kickoff
// the number is the projection; once points post it is the score with the estimated final beneath.
// A tile with a board behind it (this week and earlier) is a link to that board: the ring
// firms and a chevron closes the header on hover, the way a row in a broadcast menu lifts.
function MatchupCard({ m, final, href, now }: { m: League["scoreboard"][number]; final: boolean; href: string | null; now: number }) {
  const live = m.live;
  const on = final || live || m.home.score > 0 || m.away.score > 0;
  const lead = on ? Math.sign(m.away.score - m.home.score) : 0;
  const projected = m.home.proj > 0 || m.away.proj > 0;
  // the bar's left share (away, the top row): a final off the scores as shown, all the way to the
  // winner; before that the model's odds, and none for a week with no lineups yet
  const odds = final ? finalP(m.away.score, m.home.score) : m.win_prob?.away ?? null;
  const card = (
    <Card className={`${BOARD_CARD} ${m.home.me || m.away.me ? "ring-foreground/30" : ""} ${href ? "transition-[box-shadow,background-color] group-hover/tile:bg-foreground/[0.025] group-hover/tile:ring-foreground/35 group-focus-visible/tile:ring-2 group-focus-visible/tile:ring-ring/60" : ""}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-3 bg-foreground/[0.04] py-2.5 sm:py-3">
        <span className={`${LABEL} flex items-center gap-2`}>
          <Swap k={final ? "final" : live ? "live" : projected ? "matchup" : "upcoming"}>
            {final ? <span className="text-foreground">Final</span> : live ? <LiveChip /> : projected ? "Matchup" : "Upcoming"}
          </Swap>
        </span>
        <span className={`${LABEL} flex items-center gap-1`}>
          <Swap>{on ? "pts" : projected ? "proj" : ""}</Swap>
          {href && <ChevronRight aria-hidden className="-mr-1 size-4 text-foreground/40 transition-[color,translate] group-hover/tile:translate-x-0.5 group-hover/tile:text-foreground/80" />}
        </span>
      </CardHeader>
      <CardContent className="-mt-(--card-spacing)">
        <ul className="divide-y divide-foreground/10">
          <ScoreRow side={m.away} on={on} final={final} now={now} trailing={lead < 0} won={final && lead > 0} odds={odds} half="top" />
          <ScoreRow side={m.home} on={on} final={final} now={now} trailing={lead > 0} won={final && lead < 0} odds={odds} half="bottom" />
        </ul>
      </CardContent>
      {/* the odds as a screen reader hears them; each row draws its half of the edge stripe */}
      {odds != null && <TileOdds p={odds} teams={[m.away.team, m.home.team]} final={final} />}
    </Card>
  );
  if (!href) return card;
  return (
    <Link href={href} className="group/tile block rounded-lg outline-none">
      {card}
    </Link>
  );
}

export function LeagueView({ data, now }: { data: League; now: number }) {
  const final = data.week < data.current_week;
  const playoffs = data.playoff_weeks ?? [];
  // a playoff week has no pairings until the regular season (the weeks before the first playoff one) ends
  const empty = playoffs.includes(data.week)
    ? `Playoff matchups are set after week ${Math.min(...playoffs) - 1}.`
    : `No matchups scheduled for week ${data.week}.`;
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">League</h1>
        <StaleBadge since={data.stale_since} />
      </div>
      <section className="space-y-3">
        <SectionHeading title="Standings" />
        <Standings rows={data.standings} playoffTeams={data.playoff_teams} />
        <p className="text-xs text-muted-foreground">Top {data.playoff_teams} make the playoffs. PF and PA are season points for and against.</p>
      </section>
      <section className="space-y-3">
        <SectionHeading title="Scoreboard" count={data.scoreboard.length}>
          <LiveChip on={data.scoreboard.some((m) => m.live)} />
          <WeekSelect week={data.week} current={data.current_week} weeks={data.weeks} playoffs={data.playoff_weeks} base="/league" />
        </SectionHeading>
        {/* another week crossfades in over the last, in place (globals.css, `.swap`) */}
        <ViewTransition update="swap" default="none">
          {data.scoreboard.length === 0
            ? <Empty className="py-6"><EmptyHeader><EmptyDescription>{empty}</EmptyDescription></EmptyHeader></Empty>
            : (
              <div className="grid gap-3 md:grid-cols-2 md:gap-4">
                {data.scoreboard.map((m) => <MatchupCard key={`${m.home.team_id}-${m.away.team_id}`} m={m} final={final || m.final} href={matchupHref(m, data.week, data.current_week)} now={now} />)}
              </div>
            )}
        </ViewTransition>
      </section>
    </div>
  );
}
