import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Item, ItemActions, ItemContent, ItemDescription, ItemGroup, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import type { Player, Side, Week } from "@/lib/api";
import { injuryLabel, injuryTone, pts, signed } from "@/lib/format";
import { gamePhase, sideTotals, toPlay } from "@/lib/game";
import { clubLogoSrc } from "@/lib/teams";
import { ClubLogo } from "./club-logo";
import { Kickoff } from "./local-time";
import { PlayFeedStrip } from "./play-feed";
import { TeamLogo, TeamPanel } from "./player-media";
import { StaleBadge } from "./stale-badge";
import { StatLine } from "./stat-line";

const TAG = { ok: "secondary", warn: "warn", bad: "out" } as const;
// ESPN names the flex slot by what it takes; the board calls it FLEX (season.py SLOT_LABEL).
const SLOT_LABEL: Record<string, string> = { "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX" };
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

const BOARD_CARD = "rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]";
const LABEL = "font-display text-[13px] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// The board's grid, shared by every line so the slot column runs in one spine from the header to
// the footer: my half, the slot, the opponent's half.
const HALF_GRID = "grid grid-cols-[minmax(0,1fr)_2rem_minmax(0,1fr)] gap-x-2 sm:grid-cols-[minmax(0,1fr)_2.5rem_minmax(0,1fr)] sm:gap-x-3";

type End = "me" | "opp";

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

// The scorebug: both clubs and their totals, the way a broadcast holds the score in the corner.
// Before kickoff the projection is the number; once a game is on, the score takes the big slot
// and the projection steps beneath it with how many starters are still to play. The club's mark
// stands at the outer edge of each side with the name beside it, so the two marks frame the bug.
function Total({ side, now, end }: { side: Side; now: number; end: End }) {
  const { started: on, left } = sideState(side, now);
  const big = on ? side.actual_total : side.proj_total;
  const gain = side.optimal_total - side.proj_total;
  const opp = end === "opp";
  // Three subgrid rows (name / number / note) so the two sides line up row for row even when
  // one team name wraps and the other does not.
  return (
    <div className={`row-span-3 grid min-w-0 grid-rows-subgrid gap-y-0 ${opp ? "text-right" : ""}`}>
      <div className={`flex items-center gap-2.5 self-end sm:gap-3 ${opp ? "flex-row-reverse" : ""}`}>
        <ClubLogo src={clubLogoSrc(side)} abbrev={side.abbrev} size={40} className="sm:hidden" />
        <ClubLogo src={clubLogoSrc(side)} abbrev={side.abbrev} size={48} className="hidden sm:flex" />
        <p className="line-clamp-2 min-w-0 text-balance font-display text-[1.25rem] font-semibold leading-none tracking-tight sm:text-[1.65rem]">
          {side.team}
        </p>
      </div>
      <div className={`mt-2 flex items-baseline gap-1.5 ${opp ? "justify-end" : ""}`}>
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
    <Card className={BOARD_CARD}>
      <CardContent className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] grid-rows-[auto_auto_auto] gap-x-3 py-4 sm:gap-x-6 sm:py-5">
        <Total side={data.me} now={now} end="me" />
        <span aria-hidden className="row-span-3 self-center font-display text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground sm:text-base">vs</span>
        {data.opp
          ? <Total side={data.opp} now={now} end="opp" />
          : <p className="row-span-3 self-center text-right text-sm text-muted-foreground">No opponent this week</p>}
      </CardContent>
    </Card>
  );
}

// "at LAR" when he travels, "vs SF" at home; nothing when he has no opponent this week (the
// kickoff line beneath says BYE or "not scheduled" instead).
function Matchup({ p }: { p: Player }) {
  if (!p.opponent) return null;
  // Dot, `vs`, mark and code wrap together as one unit, so a half-width column on the phone
  // breaks to `QB · PHI` / `· vs WSH`: the dot leads the wrapped line and the code is never orphaned.
  return (
    <span className="inline-flex items-center gap-x-1 whitespace-nowrap">
      <span aria-hidden>·</span>
      {p.home !== null && <span className="font-display text-[12px] font-semibold uppercase tracking-[0.12em] text-foreground/80">{p.home ? "vs" : "at"}</span>}
      <TeamLogo abbr={p.opponent} size={16} />
      <span className="font-medium text-foreground/85">{p.opponent}</span>
    </span>
  );
}

// The number with its own unit, because on a Sunday afternoon the rows are a mix: PROJ is the
// forecast for a game still to come, PTS the posted score with the projection it replaced
// beneath it and a live mark while the game is on. `inline` is the phone form, one line inside
// the name block: `24.7 PTS · proj 20.7`.
function Num({ p, now, end, inline = false, className = "" }: { p: Player; now: number; end: End; inline?: boolean; className?: string }) {
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
  const opp = end === "opp";
  const numeral = (
    <span className="flex items-baseline gap-1.5">
      <span className={`font-display font-bold leading-none tracking-tight ${inline ? "text-[1.25rem]" : "text-[1.5rem]"} ${!on && p.proj === 0 ? "text-muted-foreground" : ""}`}>
        {pts(on ? p.actual : p.proj)}
      </span>
      <Unit fact={on} className="text-[11px]" />
    </span>
  );
  const note = (
    <span className="flex items-center gap-x-1 text-xs text-muted-foreground">
      {phase === "live" && <><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" /><span>live</span><span aria-hidden>·</span></>}
      {on
        ? <span>proj {pts(p.proj)}</span>
        : p.spread != null && p.spread >= 3
          ? <span className="font-medium text-close-foreground dark:text-close">±{pts(p.spread)}</span>
          : null}
    </span>
  );
  if (inline) {
    return (
      <p className={`flex flex-wrap items-baseline gap-x-2 tabular-nums ${opp ? "justify-end" : ""} ${className}`}>
        {numeral}
        {note}
      </p>
    );
  }
  return (
    <div className={`tabular-nums ${opp ? "text-left" : "text-right"} ${className}`}>
      <div className={`flex ${opp ? "justify-start" : "justify-end"}`}>{numeral}</div>
      <div className={`mt-0.5 flex ${opp ? "justify-start" : "justify-end"}`}>{note}</div>
    </div>
  );
}

// One player's half of a row, a shadcn Item: the club tile as its media at the outer edge, the
// name block as its content reading in toward the slot column, the number as its actions beside
// the spine (from `sm`; on the phone the number is a line inside the block). The opponent's half
// is the same Item reversed, so the two sides face each other across the board.
function Half({ p, week, now, end }: { p: Player; week: number; now: number; end: End }) {
  const opp = end === "opp";
  const hurt = !HEALTHY.has(p.injury);
  const idle = isIdle(p);
  return (
    <Item size="sm" className={`min-w-0 flex-nowrap gap-2 rounded-none border-0 p-0 sm:gap-3 ${opp ? "flex-row-reverse" : ""}`}>
      <ItemMedia className="size-9 overflow-hidden rounded-md sm:size-12 *:w-full">
        <TeamPanel abbr={p.team} playerId={p.player_id} name={p.name} muted={idle} tile />
      </ItemMedia>
      <ItemContent className={`min-w-0 gap-0 ${opp ? "items-end text-right" : ""}`}>
        <ItemTitle className={`line-clamp-none flex! max-w-full flex-wrap gap-x-2 gap-y-0.5 font-display text-[1.125rem] font-semibold leading-none tracking-tight sm:text-[1.25rem] ${opp ? "flex-row-reverse" : ""} ${idle ? "text-foreground/70" : ""}`}>
          <span className="truncate">{p.name}</span>
          {hurt && (
            <Badge variant={TAG[injuryTone(p.injury)]} shape="chip" title={p.injury_note ?? undefined}>
              {injuryLabel(p.injury.replace("_", " "), p.return_week, week)}
            </Badge>
          )}
          {p.bye && <Badge variant="muted" shape="chip">bye</Badge>}
        </ItemTitle>
        <Num p={p} now={now} end={end} inline className="mt-1 sm:hidden" />
        <ItemDescription className={`mt-1 line-clamp-none flex! flex-wrap items-center gap-x-1 text-xs leading-normal ${opp ? "justify-end text-right" : ""}`}>
          <span className="font-semibold text-foreground/80">{p.pos}</span>
          <span aria-hidden>·</span>
          <span>{p.team}</span>
          <Matchup p={p} />
        </ItemDescription>
        {/* The kickoff on its own line, so it never wraps away from the matchup mid-phrase. */}
        <ItemDescription className={`mt-0.5 line-clamp-none text-xs leading-normal tabular-nums ${opp ? "text-right" : ""}`}>
          <Kickoff iso={p.kickoff} bye={p.bye} game={p.game} team={p.team} />
        </ItemDescription>
        {/* The row's one fact once he has played: the box-score line, in the play feed's register. */}
        {p.stat_line && <StatLine text={p.stat_line} className="mt-1" wrap />}
      </ItemContent>
      <ItemActions className="hidden self-center sm:flex">
        <Num p={p} now={now} end={end} />
      </ItemActions>
    </Item>
  );
}

// One line of the board: my player, the slot he fills, the opponent's player in the same slot.
// The slot code is the spine; bench and IR lines leave it blank. An empty half (a shorter bench)
// keeps its cell so the grid stays in step.
function Pair({ a, b, slot, week, now }: { a?: Player; b?: Player; slot: string | null; week: number; now: number }) {
  return (
    <div role="listitem" className={`${HALF_GRID} items-start py-2`}>
      {a ? <Half p={a} week={week} now={now} end="me" /> : <span />}
      <span className="self-center text-center font-mono text-xs text-muted-foreground">{slot}</span>
      {b ? <Half p={b} week={week} now={now} end="opp" /> : <span />}
    </div>
  );
}

// Pairs the two sides line for line: starters slot by slot (both lineups fill the league's slots
// in the same order), bench and IR by position in the list.
function zip(a: Player[], b: Player[]): [Player | undefined, Player | undefined][] {
  const n = Math.max(a.length, b.length);
  return Array.from({ length: n }, (_, i) => [a[i], b[i]]);
}

function Group({ title, a, b, week, slot, now }: { title: string | null; a: Player[]; b: Player[]; week: number; slot: boolean; now: number }) {
  const rows = zip(a, b);
  if (rows.length === 0) return null;
  const label = (p?: Player) => (slot && p ? (SLOT_LABEL[p.slot] ?? p.slot) : null);
  return (
    <div>
      {title && (
        <div className={`${HALF_GRID} mt-2 items-baseline pt-2`}>
          <div className="flex items-baseline gap-3">
            <span className={LABEL}>{title}</span>
            <Separator className="mb-1 flex-1 self-end bg-foreground/10" />
          </div>
          <span />
          <div className="flex items-baseline gap-3">
            <Separator className="mb-1 flex-1 self-end bg-foreground/10" />
            <span className={LABEL}>{title}</span>
          </div>
        </div>
      )}
      <ItemGroup className="gap-0 divide-y divide-foreground/10">
        {rows.map(([x, y], i) => (
          <Pair key={x?.player_id ?? y?.player_id ?? i} a={x} b={y} slot={label(x) ?? label(y)} week={week} now={now} />
        ))}
      </ItemGroup>
    </div>
  );
}

// One side's total line under the starters, ruled off like a ledger: what has posted, what is
// still to come by projection (a player mid-game counts the rest of his projection, see
// sideTotals), and the two added up as the estimated final. The estimate is the figure that
// matters, so it alone is full ink and, on the phone, the only numeral: the parts step down to
// a caption beneath it. The opponent's line is mirrored so the estimates meet at the spine.
function SideTotals({ side, now, end }: { side: Side; now: number; end: End }) {
  const t = sideTotals(side.lineup, isIdle, now);
  const opp = end === "opp";
  const cell = (label: string, n: number, lead = false) => (
    <div className={`whitespace-nowrap ${opp ? "mr-5 text-left last:mr-0 sm:mr-7" : "ml-5 text-right first:ml-0 sm:ml-7"}`}>
      <p className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className={`mt-0.5 font-display text-[1.25rem] font-bold leading-none tracking-tight tabular-nums ${lead ? "" : "text-foreground/70"}`}>{pts(n)}</p>
    </div>
  );
  return (
    <div className={`border-t-2 border-foreground/35 pt-2.5 pb-1 ${opp ? "text-left" : "text-right"}`} aria-label={`${side.team} totals`}>
      <div className={`hidden items-end sm:flex ${opp ? "justify-start" : "justify-end"}`}>
        {opp ? (
          <>{cell("Est. final", t.estimate, true)}{cell("To play", t.toCome)}{cell("Scored", t.scored)}</>
        ) : (
          <>{cell("Scored", t.scored)}{cell("To play", t.toCome)}{cell("Est. final", t.estimate, true)}</>
        )}
      </div>
      <div className="sm:hidden">
        <p className="font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">Est. final</p>
        <p className="mt-0.5 font-display text-[1.25rem] font-bold leading-none tracking-tight tabular-nums">{pts(t.estimate)}</p>
        <p className="mt-1 text-xs tabular-nums text-muted-foreground">
          scored {pts(t.scored)} <span aria-hidden>·</span> to play {pts(t.toCome)}
        </p>
      </div>
    </div>
  );
}

// The lineup by projection when it differs from the one set, slot by slot; it is the same
// optimal lineup the Roster page's calls come from. The opponent's list is mirrored.
function Swaps({ side, end }: { side: Side; end: End }) {
  const opp = end === "opp";
  if (side.optimal_swaps.length === 0) return <span />;
  return (
    <div className={`flex flex-col gap-1 ${opp ? "text-right" : ""}`}>
      <p className="text-[15px] font-medium leading-snug">
        By projection {signed(side.optimal_total - side.proj_total)}
      </p>
      <ul className="space-y-0.5 text-xs text-muted-foreground">
        {side.optimal_swaps.map((s, i) => (
          // mirrored: the gain leads and the slot closes the line at the card's right edge
          <li key={i} className={`flex flex-wrap items-baseline gap-x-2 ${opp ? "justify-end" : ""}`}>
            {opp && <span className="tabular-nums text-foreground/85">{signed(s.gain)}</span>}
            {!opp && <span className="font-mono">{s.slot}</span>}
            <span>{s.out} <span aria-hidden>→</span> <span className="text-foreground/85">{s.in}</span></span>
            {opp && <span className="font-mono">{s.slot}</span>}
            {!opp && <span className="ml-auto tabular-nums text-foreground/85">{signed(s.gain)}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

// Both lineups on one board, slot for slot: my side reads in from the left edge, the opponent's
// in from the right, and the slot code runs down the spine between them, so QB sits beside QB
// and FLEX beside FLEX at every width. The header names each side with its state (PROJ, "4 TO
// PLAY", FINAL); the totals ledger rules off the starters; bench and IR step back under small
// labels; the footer is each side's lineup by projection when it differs from the one set.
function Board({ data, now }: { data: Week; now: number }) {
  const { me, opp, week } = data;
  const mine = sideState(me, now);
  const theirs = opp ? sideState(opp, now) : null;
  const swaps = me.optimal_swaps.length > 0 || (opp?.optimal_swaps.length ?? 0) > 0;
  return (
    <Card className={BOARD_CARD} aria-label="Lineups">
      <CardHeader className={`${HALF_GRID} items-baseline pt-3 sm:pt-3.5`}>
        <div className="flex min-w-0 items-baseline gap-x-2.5">
          <h2 className="min-w-0 truncate font-display text-xl font-semibold uppercase tracking-[0.14em]">My lineup</h2>
          <span className={`${LABEL} shrink-0 whitespace-nowrap tabular-nums`}>{mine.label}</span>
        </div>
        <span />
        {opp && theirs && (
          <div className="flex min-w-0 items-baseline justify-end gap-x-2.5">
            <span className={`${LABEL} shrink-0 whitespace-nowrap tabular-nums`}>{theirs.label}</span>
            {/* the full name from `sm`; the phone's half-width gets ESPN's abbreviation */}
            <h2 className="min-w-0 truncate font-display text-xl font-semibold uppercase tracking-[0.14em]">
              <span className="sm:hidden">{opp.abbrev || opp.team}</span>
              <span className="hidden sm:inline">{opp.team}</span>
            </h2>
          </div>
        )}
      </CardHeader>
      <CardContent className="-mt-1 pb-1">
        <Group title={null} a={me.lineup} b={opp?.lineup ?? []} week={week} slot now={now} />
        <div className={`${HALF_GRID} items-start`}>
          <SideTotals side={me} now={now} end="me" />
          <span />
          {opp ? <SideTotals side={opp} now={now} end="opp" /> : <span />}
        </div>
        <Group title="Bench" a={me.bench} b={opp?.bench ?? []} week={week} slot={false} now={now} />
        <Group title="IR" a={me.ir} b={opp?.ir ?? []} week={week} slot={false} now={now} />
      </CardContent>
      {swaps && (
        <CardFooter className={`${HALF_GRID} items-start rounded-none border-t border-foreground/10 bg-transparent pt-2.5 pb-3`}>
          <Swaps side={me} end="me" />
          <span />
          {opp ? <Swaps side={opp} end="opp" /> : <span />}
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
      {/* The feed only earns its place once something has happened; before kickoff it stays out of the way. */}
      {data.plays.length > 0 && <PlayFeedStrip plays={data.plays} />}
      <Board data={data} now={now} />
    </div>
  );
}
