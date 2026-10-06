import { ChevronRight, Triangle } from "lucide-react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { League, ScoreSide } from "@/lib/api";
import { BOARD_CARD, LABEL } from "@/lib/board";
import { pts, record } from "@/lib/format";
import { matchupHref } from "@/lib/matchup-link";
import { clubLogoSrc } from "@/lib/teams";
import { favourite } from "@/lib/win-prob";
import { ClubLogo } from "./club-logo";
import { SectionHeading } from "./section-heading";
import { StaleBadge } from "./stale-badge";
import { TeamLink } from "./team-link";
import { WeekSelect } from "./week-select";
import { WinMeter } from "./win-meter";

// One full-width table: rank, mark and name, then the record and the points columns in
// condensed numerals. A heavier rule under the last playoff seed marks the cut line.
function Standings({ rows, playoffTeams }: { rows: League["standings"]; playoffTeams: number }) {
  return (
    <Card className={BOARD_CARD}>
      <CardContent className="px-0 sm:px-0">
        <Table>
          <TableHeader>
            <TableRow className="border-foreground/10 hover:bg-transparent">
              <TableHead className={`${LABEL} w-10 pl-3 text-right sm:pl-4`}>#</TableHead>
              <TableHead className={LABEL}>Team</TableHead>
              <TableHead className={`${LABEL} text-right`}>W-L</TableHead>
              <TableHead className={`${LABEL} pr-3 text-right sm:pr-2`}>PF</TableHead>
              <TableHead className={`${LABEL} hidden text-right sm:table-cell`}>PA</TableHead>
              <TableHead className={`${LABEL} hidden pr-3 text-right sm:table-cell sm:pr-4`}>Strk</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((t) => (
              <TableRow
                key={t.team_id}
                className={`hover:bg-transparent ${t.me ? "bg-foreground/[0.05]" : ""} ${t.rank === playoffTeams && t.rank < rows.length ? "border-b-2 border-foreground/35" : "border-foreground/10"}`}
              >
                <TableCell className="pl-3 text-right font-display text-lg font-semibold tabular-nums text-muted-foreground sm:pl-4">{t.rank}</TableCell>
                <TableCell className="py-2 whitespace-normal">
                  <div className="flex items-center gap-2.5 sm:gap-3">
                    <ClubLogo src={clubLogoSrc(t)} abbrev={t.abbrev} size={32} />
                    <div className="min-w-0">
                      <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-display text-[1.25rem] font-semibold leading-none tracking-tight">
                        <TeamLink id={t.team_id} className="truncate">{t.team}</TeamLink>
                        {t.me && <Badge variant="muted" shape="chip">me</Badge>}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {t.abbrev}
                        {t.streak && <span className="sm:hidden"> · {t.streak}</span>}
                      </p>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="text-right font-display text-[1.25rem] font-bold tabular-nums tracking-tight">{record(t)}</TableCell>
                <TableCell className="pr-3 text-right font-display text-lg font-semibold tabular-nums tracking-tight sm:pr-2">{pts(t.points_for)}</TableCell>
                <TableCell className="hidden text-right font-display text-lg font-medium tabular-nums tracking-tight text-foreground/70 sm:table-cell">{pts(t.points_against)}</TableCell>
                <TableCell className="hidden pr-3 text-right font-display text-lg font-medium tabular-nums text-foreground/70 sm:table-cell sm:pr-4">{t.streak}</TableCell>
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
// loser keeps the caret's column empty so the two scores stay aligned.
function ScoreRow({ side, on, trailing, final, won }: { side: ScoreSide; on: boolean; trailing: boolean; final: boolean; won: boolean }) {
  return (
    <li className={`flex items-center gap-2.5 py-2 sm:gap-3 ${trailing ? "text-foreground/60" : ""}`}>
      <ClubLogo src={clubLogoSrc(side)} abbrev={side.abbrev} size={36} className={trailing ? "opacity-70" : ""} />
      <div className="min-w-0 flex-1">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 font-display text-[1.25rem] font-semibold leading-none tracking-tight">
          <span className="truncate">{side.team}</span>
          {side.me && <Badge variant="muted" shape="chip">me</Badge>}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{record(side)}</p>
      </div>
      <div className="text-right tabular-nums">
        {/* a week beyond the current one has pairings but no projection yet */}
        <p className={`font-display text-[1.5rem] font-bold leading-none tracking-tight ${!on && side.proj === 0 ? "text-muted-foreground" : ""}`}>
          {on ? pts(side.score) : side.proj > 0 ? pts(side.proj) : "—"}
        </p>
        {on && side.proj > 0 && <p className="mt-0.5 text-xs text-muted-foreground">proj {pts(side.proj)}</p>}
      </div>
      {final && (
        <span className="-ml-1 flex w-2.5 shrink-0 self-start pt-[0.4375rem] sm:-ml-1.5">
          {won && <><Triangle aria-hidden className="size-2.5 -rotate-90 fill-current" /><span className="sr-only">winner</span></>}
        </span>
      )}
    </li>
  );
}

// One matchup per card, away over home the way a broadcast stacks a scorebug. Before kickoff
// the number is the projection; once points post it is the score with the projection beneath.
// A tile with a board behind it (this week and earlier) is a link to that board: the ring
// firms and a chevron closes the header on hover, the way a row in a broadcast menu lifts.
function MatchupCard({ m, final, href }: { m: League["scoreboard"][number]; final: boolean; href: string | null }) {
  const live = m.live;
  const on = final || live || m.home.score > 0 || m.away.score > 0;
  const lead = on ? Math.sign(m.away.score - m.home.score) : 0;
  const projected = m.home.proj > 0 || m.away.proj > 0;
  const odds = final ? null : m.win_prob;
  const card = (
    <Card className={`${BOARD_CARD} ${m.home.me || m.away.me ? "ring-foreground/30" : ""} ${href ? "transition-[box-shadow,background-color] group-hover/tile:bg-foreground/[0.025] group-hover/tile:ring-foreground/35 group-focus-visible/tile:ring-2 group-focus-visible/tile:ring-ring/60" : ""}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-3 pt-2.5 sm:pt-3">
        <span className={`${LABEL} flex items-center gap-2`}>
          {final ? <span className="text-foreground">Final</span> : live ? <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge> : projected ? "Matchup" : "Upcoming"}
        </span>
        <span className={`${LABEL} flex items-center gap-1`}>
          {on ? "pts" : projected ? "proj" : ""}
          {odds && <><span aria-hidden>·</span><span className="text-foreground/80">{favourite({ abbrev: m.away.abbrev, p: odds.away }, { abbrev: m.home.abbrev, p: odds.home })}</span></>}
          {href && <ChevronRight aria-hidden className="-mr-1 size-4 text-foreground/40 transition-[color,translate] group-hover/tile:translate-x-0.5 group-hover/tile:text-foreground/80" />}
        </span>
      </CardHeader>
      <CardContent className="-mt-2 pb-1">
        <ul className="divide-y divide-foreground/10">
          <ScoreRow side={m.away} on={on} trailing={lead < 0} final={final} won={final && lead > 0} />
          <ScoreRow side={m.home} on={on} trailing={lead > 0} final={final} won={final && lead < 0} />
        </ul>
      </CardContent>
      {/* the odds along the card's foot, away (the top row) from the left; gone once it is final */}
      {odds && <div className="-mt-(--card-spacing)"><WinMeter p={odds.away} team={m.away.team} size="tile" /></div>}
    </Card>
  );
  if (!href) return card;
  return (
    <Link href={href} className="group/tile block rounded-lg outline-none">
      {card}
    </Link>
  );
}

export function LeagueView({ data }: { data: League }) {
  const final = data.week < data.current_week;
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
          {data.scoreboard.some((m) => m.live) && <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge>}
          <WeekSelect week={data.week} current={data.current_week} weeks={data.weeks} base="/league" />
        </SectionHeading>
        {data.scoreboard.length === 0
          ? <p className="text-sm text-muted-foreground">No matchups scheduled for week {data.week}.</p>
          : (
            <div className="grid gap-3 md:grid-cols-2 md:gap-4">
              {data.scoreboard.map((m) => <MatchupCard key={`${m.home.team_id}-${m.away.team_id}`} m={m} final={final || m.final} href={matchupHref(m, data.week, data.current_week)} />)}
            </div>
          )}
      </section>
    </div>
  );
}
