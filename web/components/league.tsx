import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Club, League, ScoreSide } from "@/lib/api";
import { pts } from "@/lib/format";
import { clubLogoSrc } from "@/lib/teams";
import { ClubLogo } from "./club-logo";
import { StaleBadge } from "./stale-badge";
import { WeekSelect } from "./week-select";

const BOARD_CARD = "rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]";
const LABEL = "font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

function record(t: Club) {
  return `${t.wins}-${t.losses}${t.ties ? `-${t.ties}` : ""}`;
}

// The section heading the Roster page uses: condensed caps, a muted count, a hairline running
// out; `children` sit at the far end of the line (the scoreboard's week picker).
function Heading({ title, count, children }: { title: string; count?: number; children?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <h2 className="font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</h2>
      {count != null && <span className="font-display text-base font-medium tabular-nums text-muted-foreground">{count}</span>}
      <Separator className="flex-1 bg-foreground/12" />
      {children}
    </div>
  );
}

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
                        <span className="truncate">{t.team}</span>
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
// the trailing side steps back to 60% ink so the leader reads first.
function ScoreRow({ side, on, trailing }: { side: ScoreSide; on: boolean; trailing: boolean }) {
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
    </li>
  );
}

// One matchup per card, away over home the way a broadcast stacks a scorebug. Before kickoff
// the number is the projection; once points post it is the score with the projection beneath.
function MatchupCard({ m, final, live }: { m: League["scoreboard"][number]; final: boolean; live: boolean }) {
  const on = final || live || m.home.score > 0 || m.away.score > 0;
  const lead = on ? Math.sign(m.away.score - m.home.score) : 0;
  const projected = m.home.proj > 0 || m.away.proj > 0;
  return (
    <Card className={`${BOARD_CARD} ${m.home.me || m.away.me ? "ring-foreground/30" : ""}`}>
      <CardHeader className="flex flex-row items-center justify-between gap-3 pt-2.5 sm:pt-3">
        <span className={`${LABEL} flex items-center gap-2`}>
          {final ? "Final" : live ? <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge> : projected ? "Matchup" : "Upcoming"}
        </span>
        <span className={LABEL}>{on ? "pts" : projected ? "proj" : ""}</span>
      </CardHeader>
      <CardContent className="-mt-2 pb-1">
        <ul className="divide-y divide-foreground/10">
          <ScoreRow side={m.away} on={on} trailing={lead < 0} />
          <ScoreRow side={m.home} on={on} trailing={lead > 0} />
        </ul>
      </CardContent>
    </Card>
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
        <Heading title="Standings" />
        <Standings rows={data.standings} playoffTeams={data.playoff_teams} />
        <p className="text-xs text-muted-foreground">Top {data.playoff_teams} make the playoffs. PF and PA are season points for and against.</p>
      </section>
      <section className="space-y-3">
        <Heading title="Scoreboard" count={data.scoreboard.length}>
          {data.live && <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge>}
          <WeekSelect week={data.week} current={data.current_week} weeks={data.weeks} base="/league" />
        </Heading>
        {data.scoreboard.length === 0
          ? <p className="text-sm text-muted-foreground">No matchups scheduled for week {data.week}.</p>
          : (
            <div className="grid gap-3 md:grid-cols-2 md:gap-4">
              {data.scoreboard.map((m) => <MatchupCard key={`${m.home.team_id}-${m.away.team_id}`} m={m} final={final} live={data.live} />)}
            </div>
          )}
      </section>
    </div>
  );
}
