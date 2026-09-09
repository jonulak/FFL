import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { Player, Side, Week } from "@/lib/api";
import { injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { Kickoff } from "./local-time";
import { TeamLogo, TeamPanel } from "./player-media";
import { StaleBadge } from "./stale-badge";

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN names the flex slot by what it takes; the board calls it FLEX (season.py SLOT_LABEL).
const SLOT_LABEL: Record<string, string> = { "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX" };
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

// A side has posted points once any of its players has, or once the week is flagged live.
function posted(side: Side, live: boolean) {
  return live || side.actual_total > 0;
}

// The scorebug: both teams and their totals, the way a broadcast holds the score in the corner.
// Before kickoff the projection is the number; once points post, the score takes the big slot
// and the projection steps beneath it.
function Total({ side, live, align }: { side: Side; live: boolean; align: "start" | "end" }) {
  const on = posted(side, live);
  const big = on ? side.actual_total : side.proj_total;
  const gain = side.optimal_total - side.proj_total;
  const end = align === "end";
  // Three subgrid rows (name / number / note) so the two sides line up row for row even when
  // one team name wraps and the other does not.
  return (
    <div className={`row-span-3 grid min-w-0 grid-rows-subgrid gap-y-0 ${end ? "text-right" : ""}`}>
      <p className={`self-end line-clamp-2 text-balance font-display text-[1.25rem] font-semibold leading-none tracking-tight sm:text-[1.65rem]`}>
        {side.team}
      </p>
      <div className={`mt-2 flex items-baseline gap-1.5 ${end ? "justify-end" : ""}`}>
        <span className="font-display text-[2.6rem] font-bold leading-none tracking-tight sm:text-[3.25rem]">{pts(big)}</span>
        <span className="font-display text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground">{on ? "pts" : "proj"}</span>
      </div>
      <p className="mt-1.5 min-h-4 text-xs tabular-nums text-muted-foreground">
        {on && <span className="mr-2">proj {pts(side.proj_total)}</span>}
        {gain > 0.05 && <span>by projection {pts(side.optimal_total)} <span className="text-foreground/80">({signed(gain)})</span></span>}
      </p>
    </div>
  );
}

function Scorebug({ data }: { data: Week }) {
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]">
      <CardContent className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] grid-rows-[auto_auto_auto] gap-x-3 py-4 sm:gap-x-6 sm:py-5">
        <Total side={data.me} live={data.live} align="start" />
        <span aria-hidden className="row-span-3 self-center font-display text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground sm:text-base">vs</span>
        {data.opp
          ? <Total side={data.opp} live={data.live} align="end" />
          : <p className="row-span-3 self-center text-right text-sm text-muted-foreground">No opponent this week</p>}
      </CardContent>
    </Card>
  );
}

// "at LAR" when he travels, "vs SF" at home; a bye or an unscheduled player says so instead.
function Matchup({ p }: { p: Player }) {
  if (!p.opponent) return <Kickoff iso={p.kickoff} bye={p.bye} />;
  // `contents`: the pieces wrap as items of the row's own flex line, not as one unbreakable block
  return (
    <span className="contents">
      {p.home !== null && <span className="font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-foreground/80">{p.home ? "vs" : "at"}</span>}
      <TeamLogo abbr={p.opponent} size={16} />
      <span className="font-medium text-foreground/85">{p.opponent}</span>
      <span aria-hidden>·</span>
      <Kickoff iso={p.kickoff} bye={p.bye} />
    </span>
  );
}

// One player, one row: the roster card cut down to a line. The team-colour tile keeps the
// board's memorable element at row scale; the number on the right is the projection, or the
// posted score with the projection under it once his game is on.
function Row({ p, week, slot }: { p: Player; week: number; slot: boolean }) {
  const hurt = !HEALTHY.has(p.injury);
  const idle = p.bye || injuryTone(p.injury) === "bad";
  const on = p.game_played > 0 || p.actual > 0;
  return (
    <li className="grid grid-cols-[2rem_2.75rem_minmax(0,1fr)_auto] items-center gap-x-2.5 py-2 sm:grid-cols-[2.25rem_3rem_minmax(0,1fr)_auto] sm:gap-x-3">
      <span className="font-mono text-xs text-muted-foreground">{slot ? (SLOT_LABEL[p.slot] ?? p.slot) : ""}</span>
      <div className="aspect-square overflow-hidden rounded-md">
        <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={idle} tile />
      </div>
      <div className="min-w-0">
        <p className={`flex flex-wrap items-center gap-x-2 gap-y-0.5 font-display text-[1.25rem] font-semibold leading-none tracking-tight ${idle ? "text-foreground/70" : ""}`}>
          <span className="truncate">{p.name}</span>
          {hurt && (
            <Badge variant={TAG[injuryTone(p.injury)]} shape="chip" title={p.injury_note ?? undefined}>
              {injuryLabel(p.injury.replace("_", " "), p.return_week, week)}
            </Badge>
          )}
          {p.bye && <Badge variant="muted" shape="chip">bye</Badge>}
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
          <span className="font-semibold text-foreground/80">{p.pos}</span>
          <span aria-hidden>·</span>
          <span>{p.team}</span>
          <span aria-hidden>·</span>
          <Matchup p={p} />
        </p>
      </div>
      <div className="text-right tabular-nums">
        <p className={`font-display text-[1.5rem] font-bold leading-none tracking-tight ${!on && p.proj === 0 ? "text-muted-foreground" : ""}`}>
          {pts(on ? p.actual : p.proj)}
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {on
            ? <>proj {pts(p.proj)}</>
            : p.spread != null && p.spread >= 3
              ? <span className="font-medium text-close-foreground dark:text-close">±{pts(p.spread)}</span>
              : null}
        </p>
      </div>
    </li>
  );
}

function Group({ title, rows, week, slot }: { title: string | null; rows: Player[]; week: number; slot: boolean }) {
  if (rows.length === 0) return null;
  return (
    <div>
      {title && (
        <div className="mt-2 flex items-baseline gap-3 pt-2">
          <span className="font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{title}</span>
          <Separator className="mb-1 flex-1 self-end bg-foreground/10" />
        </div>
      )}
      <ul className="divide-y divide-foreground/10">
        {rows.map((p) => <Row key={p.player_id} p={p} week={week} slot={slot} />)}
      </ul>
    </div>
  );
}

// One team's lineup as a board panel: starters with their slot codes, then bench and IR stepped
// back under small labels. The footer is the lineup by projection when it differs from the one
// set, slot by slot; it is the same optimal lineup the Roster page's calls come from.
function SideCard({ side, live, week, mine }: { side: Side; live: boolean; week: number; mine: boolean }) {
  const on = posted(side, live);
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]" aria-label={side.team}>
      <CardHeader className="flex flex-row items-baseline justify-between gap-3 pt-3 sm:pt-3.5">
        <h2 className="min-w-0 truncate font-display text-xl font-semibold uppercase tracking-[0.14em]">{mine ? "My lineup" : side.team}</h2>
        <span className="shrink-0 font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{on ? "pts" : "proj"}</span>
      </CardHeader>
      <CardContent className="-mt-1 pb-1">
        <Group title={null} rows={side.lineup} week={week} slot />
        <Group title="Bench" rows={side.bench} week={week} slot={false} />
        <Group title="IR" rows={side.ir} week={week} slot={false} />
      </CardContent>
      {side.optimal_swaps.length > 0 && (
        <CardFooter className="flex flex-col items-stretch gap-1 rounded-none border-t border-foreground/10 bg-transparent pt-2.5 pb-3">
          <p className="text-[15px] font-medium leading-snug">
            By projection {signed(side.optimal_total - side.proj_total)}
          </p>
          <ul className="space-y-0.5 text-xs text-muted-foreground">
            {side.optimal_swaps.map((s, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-mono">{s.slot}</span>
                <span>{s.out} <span aria-hidden>→</span> <span className="text-foreground/85">{s.in}</span></span>
                <span className="ml-auto tabular-nums text-foreground/85">{signed(s.gain)}</span>
              </li>
            ))}
          </ul>
        </CardFooter>
      )}
    </Card>
  );
}

export function MatchupView({ data }: { data: Week }) {
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <h1 className="font-display text-4xl font-bold leading-none tracking-tight">
          Matchup
          <span className="ml-2.5 font-semibold text-muted-foreground">Week {data.week}</span>
        </h1>
        {data.live && <Badge variant="muted" shape="chip"><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" />live</Badge>}
        <StaleBadge since={data.stale_since} />
      </div>
      <Scorebug data={data} />
      <div className="grid gap-3 md:grid-cols-2 md:items-start md:gap-4">
        <SideCard side={data.me} live={data.live} week={data.week} mine />
        {data.opp && <SideCard side={data.opp} live={data.live} week={data.week} mine={false} />}
      </div>
    </div>
  );
}
