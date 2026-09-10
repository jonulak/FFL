import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import type { Player, Side, Week } from "@/lib/api";
import { injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { gamePhase, sideTotals, toPlay } from "@/lib/game";
import { Kickoff } from "./local-time";
import { TeamLogo, TeamPanel } from "./player-media";
import { StaleBadge } from "./stale-badge";

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN names the flex slot by what it takes; the board calls it FLEX (season.py SLOT_LABEL).
const SLOT_LABEL: Record<string, string> = { "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX" };
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

// Idle for the week: on bye, or OUT/IR/SUSP. His game cannot add points.
const isIdle = (p: Player) => p.bye || injuryTone(p.injury) === "bad";

// Where a side stands: nothing started yet, some starters still to play, or every game final.
// A side has started once any starter's game is on or any points have posted.
function sideState(side: Side, now: number) {
  const started = side.actual_total > 0 || side.lineup.some((p) => gamePhase(p, now) !== "pending");
  const left = toPlay(side.lineup, isIdle, now);
  return { started, left, label: !started ? "proj" : left > 0 ? `${left} to play` : "final" };
}

// The small uppercase unit on a numeral's baseline: PROJ for a forecast, PTS for a posted score.
function Unit({ fact, className = "" }: { fact: boolean; className?: string }) {
  return <span className={`font-display font-semibold uppercase tracking-[0.12em] text-muted-foreground ${className}`}>{fact ? "pts" : "proj"}</span>;
}

// The scorebug: both teams and their totals, the way a broadcast holds the score in the corner.
// Before kickoff the projection is the number; once a game is on, the score takes the big slot
// and the projection steps beneath it with how many starters are still to play.
function Total({ side, now, align }: { side: Side; now: number; align: "start" | "end" }) {
  const { started: on, left } = sideState(side, now);
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
        <Unit fact={on} className="text-sm" />
      </div>
      {/* Two facts, each on its own line, so they never read as one sentence. */}
      <p className="mt-1.5 min-h-4 text-xs tabular-nums text-muted-foreground">
        {on && <span className="block whitespace-nowrap">proj {pts(side.proj_total)} <span aria-hidden>·</span> {left > 0 ? `${left} to play` : "final"}</span>}
        {gain > 0.05 && <span className="block whitespace-nowrap">by projection {pts(side.optimal_total)} <span className="text-foreground/80">({signed(gain)})</span></span>}
      </p>
    </div>
  );
}

function Scorebug({ data, now }: { data: Week; now: number }) {
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]">
      <CardContent className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] grid-rows-[auto_auto_auto] gap-x-3 py-4 sm:gap-x-6 sm:py-5">
        <Total side={data.me} now={now} align="start" />
        <span aria-hidden className="row-span-3 self-center font-display text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground sm:text-base">vs</span>
        {data.opp
          ? <Total side={data.opp} now={now} align="end" />
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
// board's memorable element at row scale. The number on the right carries its own unit, because
// on a Sunday afternoon the rows are a mix: PROJ is the forecast for a game still to come, PTS is
// the posted score, with the projection it replaced beneath it and a live mark while the game is on.
function Row({ p, week, slot, now }: { p: Player; week: number; slot: boolean; now: number }) {
  const hurt = !HEALTHY.has(p.injury);
  const idle = isIdle(p);
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
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
        <p className="flex items-baseline justify-end gap-1.5">
          <span className={`font-display text-[1.5rem] font-bold leading-none tracking-tight ${!on && p.proj === 0 ? "text-muted-foreground" : ""}`}>
            {pts(on ? p.actual : p.proj)}
          </span>
          <Unit fact={on} className="text-[11px]" />
        </p>
        <p className="mt-0.5 flex items-center justify-end gap-x-1 text-xs text-muted-foreground">
          {phase === "live" && <><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" /><span>live</span><span aria-hidden>·</span></>}
          {on
            ? <span>proj {pts(p.proj)}</span>
            : p.spread != null && p.spread >= 3
              ? <span className="font-medium text-close-foreground dark:text-close">±{pts(p.spread)}</span>
              : null}
        </p>
      </div>
    </li>
  );
}

function Group({ title, rows, week, slot, now }: { title: string | null; rows: Player[]; week: number; slot: boolean; now: number }) {
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
        {rows.map((p) => <Row key={p.player_id} p={p} week={week} slot={slot} now={now} />)}
      </ul>
    </div>
  );
}

// The total line under the starters, ruled off like a ledger: what has posted, what is still to
// come by projection (a player mid-game counts the rest of his projection, see sideTotals), and
// the two added up as the estimated final. The estimate is the figure that matters, so it alone
// is full ink.
function Totals({ side, now }: { side: Side; now: number }) {
  const t = sideTotals(side.lineup, isIdle, now);
  // Cells are spaced by their own left margin rather than a flex gap, so no browser can pack them.
  const cell = (label: string, n: number, lead = false) => (
    <div className="ml-5 text-right whitespace-nowrap first:ml-0 sm:ml-7">
      <p className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className={`mt-0.5 font-display text-[1.25rem] font-bold leading-none tracking-tight tabular-nums ${lead ? "" : "text-foreground/70"}`}>{pts(n)}</p>
    </div>
  );
  return (
    <div className="flex items-end justify-end border-t-2 border-foreground/35 pt-2.5 pb-1" aria-label="Totals">
      {cell("Scored", t.scored)}
      {cell("To play", t.toCome)}
      {cell("Est. final", t.estimate, true)}
    </div>
  );
}

// One team's lineup as a board panel: starters with their slot codes, then bench and IR stepped
// back under small labels. The header's right-hand label is the side's state (PROJ, "4 TO PLAY",
// FINAL), since once games start the rows carry their own units. The footer is the lineup by
// projection when it differs from the one set, slot by slot; it is the same optimal lineup the
// Roster page's calls come from.
function SideCard({ side, week, mine, now }: { side: Side; week: number; mine: boolean; now: number }) {
  const { label } = sideState(side, now);
  return (
    <Card className="rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]" aria-label={side.team}>
      <CardHeader className="flex flex-row items-baseline justify-between gap-3 pt-3 sm:pt-3.5">
        <h2 className="min-w-0 truncate font-display text-xl font-semibold uppercase tracking-[0.14em]">{mine ? "My lineup" : side.team}</h2>
        <span className="shrink-0 font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-muted-foreground tabular-nums">{label}</span>
      </CardHeader>
      <CardContent className="-mt-1 pb-1">
        <Group title={null} rows={side.lineup} week={week} slot now={now} />
        <Totals side={side} now={now} />
        <Group title="Bench" rows={side.bench} week={week} slot={false} now={now} />
        <Group title="IR" rows={side.ir} week={week} slot={false} now={now} />
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

// `now` is the clock the game phases are read against; the live wrapper advances it on each poll.
export function MatchupView({ data, now }: { data: Week; now: number }) {
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
      <Scorebug data={data} now={now} />
      <div className="grid gap-3 md:grid-cols-2 md:items-start md:gap-4">
        <SideCard side={data.me} week={data.week} mine now={now} />
        {data.opp && <SideCard side={data.opp} week={data.week} mine={false} now={now} />}
      </div>
    </div>
  );
}
