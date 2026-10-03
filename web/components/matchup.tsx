"use client";

import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Item, ItemActions, ItemContent, ItemDescription, ItemFooter, ItemGroup, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import type { Player, Side, Week } from "@/lib/api";
import { injuryTone, pts, shortName, signed } from "@/lib/format";
import { gamePhase, sideTotals, toPlay } from "@/lib/game";
import { clubLogoSrc } from "@/lib/teams";
import { ClubLogo } from "./club-logo";
import { BoxScoreDialog } from "./box-score-dialog";
import { GameLine } from "./local-time";
import { PlayFeedStrip } from "./play-feed";
import { StaleBadge } from "./stale-badge";
import { StatLine } from "./stat-line";

// ESPN names the flex slot by what it takes; the board calls it FLEX (season.py SLOT_LABEL).
const SLOT_LABEL: Record<string, string> = { "RB/WR/TE": "FLEX", "RB/WR": "FLEX", "WR/TE": "FLEX" };
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);

const BOARD_CARD = "rounded-lg py-0 shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)] [--card-spacing:--spacing(3)] sm:[--card-spacing:--spacing(4)]";
const LABEL = "font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// The board's grid, shared by every line so the slot column runs in one spine from the header to
// the footer: my half, the slot, the opponent's half.
const HALF_GRID = "grid grid-cols-[minmax(0,1fr)_1.75rem_minmax(0,1fr)] gap-x-1 sm:grid-cols-[minmax(0,1fr)_2.5rem_minmax(0,1fr)] sm:gap-x-3";
// A week with no opponent (a bye or an unpaired playoff week): my half and the spine, no empty right half.
const SOLO_GRID = "grid grid-cols-[minmax(0,1fr)_1.75rem] gap-x-1 sm:grid-cols-[minmax(0,1fr)_2.5rem] sm:gap-x-3";

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
        <p className="line-clamp-2 min-w-0 text-balance font-display text-[1.375rem] font-semibold leading-none tracking-tight sm:text-[1.65rem]">
          {side.team}
        </p>
      </div>
      {/* From `sm` the totals meet at VS, so the one comparison the bug exists for is one glance,
          not a jump across the card; the marks and names stay at the outer edges. */}
      <div className={`mt-2 flex items-baseline gap-1.5 ${opp ? "justify-end sm:justify-start" : "sm:justify-end"}`}>
        <span className="font-display text-[2.6rem] font-bold leading-none tracking-tight sm:text-[3.25rem]">{pts(big)}</span>
        <Unit fact={on} className="text-sm" />
      </div>
      {/* Two facts, each on its own line, so they never read as one sentence. */}
      <p className={`mt-2 min-h-4 text-xs tabular-nums text-muted-foreground ${opp ? "sm:text-left" : "sm:text-right"}`}>
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

// A player's injury status is ESPN's own one-letter mark beside the name, in the status colour
// with no chip behind it: amber for a game-time call, red for out. The full status is its title
// (and the injury note when ESPN has one); the return week is on the game line.
const TONE = { ok: "text-muted-foreground", warn: "text-close-foreground dark:text-close", bad: "text-out" } as const;
const STATUS_SHORT: Record<string, string> = { QUESTIONABLE: "Q", DOUBTFUL: "D", OUT: "O", "INJURY RESERVE": "IR", SUSPENSION: "SUSP" };

// The number with its own unit, because on a Sunday afternoon the rows are a mix: PROJ is the
// forecast for a game still to come, PTS the posted score with the projection it replaced
// beneath it and a live mark while the game is on. The numeral is 1.125rem on the phone,
// 1.375rem from `sm` with the unit beside it and the note beneath; on the phone the unit and
// note close the game line instead (NumNote), so that line has the half's whole width.
function Num({ p, now, end }: { p: Player; now: number; end: End }) {
  const on = gamePhase(p, now) !== "pending";
  const opp = end === "opp";
  return (
    <div className={`tabular-nums ${opp ? "text-left" : "text-right"}`}>
      <div className={`flex items-baseline gap-1.5 ${opp ? "justify-start" : "justify-end"}`}>
        <span className={`font-display text-[1.125rem] font-bold leading-none tracking-tight sm:text-[1.375rem] ${!on && p.proj === 0 ? "text-muted-foreground" : ""}`}>
          {pts(on ? p.actual : p.proj)}
        </span>
        <Unit fact={on} className="hidden text-[0.6875rem] sm:inline" />
      </div>
      <div className={`mt-0.5 flex max-sm:hidden ${opp ? "justify-start" : "justify-end"}`}><NumNote p={p} now={now} /></div>
    </div>
  );
}

// What stands under the number: before kickoff the source spread when it is 3+ (and, on the
// phone, the PROJ unit); once his game is on, the projection it replaced, after a live dot.
function NumNote({ p, now }: { p: Player; now: number }) {
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
  return (
    <span className="flex shrink-0 items-center gap-x-1 whitespace-nowrap text-[0.6875rem] leading-4 tabular-nums text-muted-foreground sm:text-xs">
      {phase === "live" && <><span aria-hidden className="size-1.5 rounded-full bg-foreground/75" /><span className="max-sm:sr-only">live</span><span aria-hidden className="max-sm:hidden">·</span></>}
      {!on && <Unit fact={false} className="sm:hidden" />}
      {on
        ? <span>proj {pts(p.proj)}</span>
        : p.spread != null && p.spread >= 3
          ? <span className="font-medium text-close-foreground max-sm:hidden dark:text-close">±{pts(p.spread)}</span>
          : null}
    </span>
  );
}

// Opens a player's scoring breakdown; the board passes it down to every half.
type Pick = (p: Player) => void;

// One player's half of a row, a shadcn Item rendered as a button (a tap opens his scoring
// breakdown): the name block reading in toward the slot column, the number as its actions
// beside the spine. No photo: the name, the game line and the number are the row, and the
// half column is too narrow to spend on a portrait. The opponent's half is the same Item
// reversed, so the two sides face each other across the board.
function Half({ p, week, now, end, onPick }: { p: Player; week: number; now: number; end: End; onPick: Pick }) {
  const opp = end === "opp";
  const hurt = !HEALTHY.has(p.injury);
  const status = p.injury.replaceAll("_", " ");
  const back = p.return_week != null && p.return_week > week ? p.return_week : null;
  const idle = isIdle(p);
  const on = gamePhase(p, now) !== "pending";
  // The phone half: the short name (`B. Purdy`) with a one-letter status chip, then the game on
  // one line, `[SF] vs [DEN] Sun 4:25 PM` or `24 [PIT] @ 27 [CLE] Final`, and once he has played
  // the lead stat group (passing for a QB, rushing for a RB, receiving for a WR) as the footer,
  // with the whole half to itself. From `sm` the full name, the position before the game line,
  // the whole stat line.
  return (
    <Item
      size="sm"
      render={<button type="button" onClick={() => onPick(p)} aria-label={`${p.name}: scoring breakdown`} />}
      className={`min-w-0 cursor-pointer flex-wrap gap-x-2 gap-y-1 rounded-md border-0 px-1 py-1 text-left outline-none transition-colors hover:bg-foreground/[0.04] focus-visible:ring-2 focus-visible:ring-ring/60 sm:flex-nowrap sm:gap-3 sm:px-1.5 ${opp ? "-mr-1 flex-row-reverse sm:-mr-1.5" : "-ml-1 sm:-ml-1.5"}`}
    >
      <ItemContent className={`min-w-0 gap-0 ${opp ? "items-end text-right" : ""}`}>
        <ItemTitle className={`line-clamp-none flex! max-w-full items-center gap-x-1.5 font-display text-[1rem] font-semibold leading-none tracking-tight sm:flex-wrap sm:gap-x-2 sm:gap-y-0.5 sm:text-[1.125rem] ${opp ? "flex-row-reverse" : ""} ${idle ? "text-foreground/70" : ""}`}>
          <span className="min-w-0 truncate"><span className="sm:hidden">{shortName(p.name)}</span><span className="hidden sm:inline">{p.name}</span></span>
          {hurt && (
            <span title={p.injury_note ?? status} className={`shrink-0 font-bold ${TONE[injuryTone(p.injury)]}`}>
              <span aria-hidden>{STATUS_SHORT[status] ?? status}</span>
              <span className="sr-only">{status.toLowerCase()}</span>
            </span>
          )}
          {p.bye && <Badge variant="muted" shape="chip">bye</Badge>}
        </ItemTitle>
        {/* `max-sm:hidden!`: the `flex!` that beats shadcn's line-clamp reset would beat a plain `max-sm:hidden` too */}
        <ItemDescription className={`mt-1 line-clamp-none flex! flex-wrap items-center gap-x-1.5 text-xs leading-4 tabular-nums max-sm:hidden! ${opp ? "justify-end text-right" : ""}`}>
          <span className="font-semibold text-foreground/80">{p.pos}</span>
          {/* the bye says itself in the chip; the line keeps only a real game */}
          {!p.bye && <GameLine p={p} size={14} />}
          {hurt && back != null && <span className="whitespace-nowrap">back wk {back}</span>}
        </ItemDescription>
        {/* The row's one fact once he has played: the box-score line, in the play feed's register,
            wrapping at its groups. The phone has the lead group only, as the footer below. */}
        {p.stat_line && <StatLine text={p.stat_line} className="mt-1.5 text-xs max-sm:hidden" wrap />}
      </ItemContent>
      <ItemActions className="self-start">
        <Num p={p} now={now} end={end} />
      </ItemActions>
      {/* The phone's game line spans the half, the PROJ unit at its inner end by the spine; the
          opponent's is mirrored, his game at the outer edge. Once his game is on the score line
          needs the width, so the note (`proj 17.8`) closes the stat line beneath instead. */}
      <ItemFooter className={`-mt-0.5 gap-x-1.5 text-[0.6875rem] leading-4 text-muted-foreground sm:hidden ${opp ? "flex-row-reverse" : ""}`}>
        <span className={`flex min-w-0 flex-wrap items-center gap-x-1.5 ${opp ? "justify-end" : ""}`}>
          {!p.bye && <GameLine p={p} size={13} />}
          {hurt && back != null && <span className="whitespace-nowrap">back wk {back}</span>}
        </span>
        {!on && <NumNote p={p} now={now} />}
      </ItemFooter>
      {on && (
        <ItemFooter className={`items-start gap-x-2 sm:hidden ${opp ? "flex-row-reverse" : ""}`}>
          {p.stat_line
            ? <StatLine text={p.stat_line.split(" · ")[0]} className={`min-w-0 text-[0.6875rem] ${opp ? "text-right" : ""}`} title={p.stat_line} wrap />
            : <span />}
          <NumNote p={p} now={now} />
        </ItemFooter>
      )}
    </Item>
  );
}

// One line of the board: my player, the slot he fills, the opponent's player in the same slot.
// The slot code is the spine; bench and IR lines leave it blank. An empty half (a shorter bench)
// keeps its cell so the grid stays in step.
function Pair({ a, b, slot, week, now, grid, onPick }: { a?: Player; b?: Player; slot: string | null; week: number; now: number; grid: string; onPick: Pick }) {
  return (
    <div role="listitem" className={`${grid} items-start py-1`}>
      {a ? <Half p={a} week={week} now={now} end="me" onPick={onPick} /> : <span />}
      <span className="self-center text-center font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground sm:text-xs">{slot}</span>
      {grid === HALF_GRID && (b ? <Half p={b} week={week} now={now} end="opp" onPick={onPick} /> : <span />)}
    </div>
  );
}

// Pairs the two sides line for line: starters slot by slot (both lineups fill the league's slots
// in the same order), bench and IR by position in the list.
function zip(a: Player[], b: Player[]): [Player | undefined, Player | undefined][] {
  const n = Math.max(a.length, b.length);
  return Array.from({ length: n }, (_, i) => [a[i], b[i]]);
}

function Group({ title, a, b, week, slot, now, grid, onPick }: { title: string | null; a: Player[]; b: Player[]; week: number; slot: boolean; now: number; grid: string; onPick: Pick }) {
  const rows = zip(a, b);
  if (rows.length === 0) return null;
  const label = (p?: Player) => (slot && p ? (SLOT_LABEL[p.slot] ?? p.slot) : null);
  return (
    <div>
      {title && (
        <div className={`${grid} mt-2 items-baseline pt-2`}>
          <div className="flex items-baseline gap-3">
            <span className={LABEL}>{title}</span>
            <Separator className="mb-1 flex-1 self-end bg-foreground/10" />
          </div>
          {grid === HALF_GRID && (
            <>
              <span />
              <div className="flex items-baseline gap-3">
                <Separator className="mb-1 flex-1 self-end bg-foreground/10" />
                <span className={LABEL}>{title}</span>
              </div>
            </>
          )}
        </div>
      )}
      <ItemGroup className="gap-0 divide-y divide-foreground/10">
        {rows.map(([x, y], i) => (
          <Pair key={x?.player_id ?? y?.player_id ?? i} a={x} b={y} slot={label(x) ?? label(y)} week={week} now={now} grid={grid} onPick={onPick} />
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
      <p className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className={`mt-0.5 font-display text-[1.25rem] font-bold leading-none tracking-tight tabular-nums ${lead ? "" : "text-foreground/70"}`}>{pts(n)}</p>
    </div>
  );
  return (
    <div className={`border-t-2 border-foreground/35 pt-3 pb-1 ${opp ? "text-left" : "text-right"}`} aria-label={`${side.team} totals`}>
      <div className={`hidden items-end sm:flex ${opp ? "justify-start" : "justify-end"}`}>
        {opp ? (
          <>{cell("Est. final", t.estimate, true)}{cell("To play", t.toCome)}{cell("Scored", t.scored)}</>
        ) : (
          <>{cell("Scored", t.scored)}{cell("To play", t.toCome)}{cell("Est. final", t.estimate, true)}</>
        )}
      </div>
      <div className="sm:hidden">
        <p className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Est. final</p>
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
      <p className="text-[0.9375rem] font-medium leading-snug">
        By projection {signed(side.optimal_total - side.proj_total)}
      </p>
      <ul className="space-y-0.5 text-xs text-muted-foreground">
        {side.optimal_swaps.map((s, i) => (
          // mirrored: the gain leads and the slot closes the line at the card's right edge
          <li key={i} className={`flex flex-wrap items-baseline gap-x-2 ${opp ? "justify-end" : ""}`}>
            {opp && <span className="tabular-nums text-foreground/85">{signed(s.gain)}</span>}
            {!opp && <span className="font-mono">{SLOT_LABEL[s.slot] ?? s.slot}</span>}
            {/* initial and surname on the phone, where the half column cannot hold two full names */}
            <span>
              <span className="sm:hidden">{shortName(s.out)} <span aria-hidden>→</span> <span className="text-foreground/85">{shortName(s.in)}</span></span>
              <span className="max-sm:hidden">{s.out} <span aria-hidden>→</span> <span className="text-foreground/85">{s.in}</span></span>
            </span>
            {opp && <span className="font-mono">{SLOT_LABEL[s.slot] ?? s.slot}</span>}
            {!opp && <span className="ml-auto tabular-nums text-foreground/85">{signed(s.gain)}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

// A side's name in the board header: the full name from `sm`; the phone's half-width gets ESPN's abbreviation.
function TeamName({ side }: { side: Side }) {
  return (
    <>
      <span className="sm:hidden">{side.abbrev || side.team}</span>
      <span className="hidden sm:inline">{side.team}</span>
    </>
  );
}

// Both lineups on one board, slot for slot: my side reads in from the left edge, the opponent's
// in from the right, and the slot code runs down the spine between them, so QB sits beside QB
// and FLEX beside FLEX at every width. The header names each side with its state (PROJ, "4 TO
// PLAY", FINAL); the totals ledger rules off the starters; bench and IR step back under small
// labels; the footer is each side's lineup by projection when it differs from the one set.
function Board({ data, now, onPick }: { data: Week; now: number; onPick: Pick }) {
  const { me, opp, week } = data;
  const mine = sideState(me, now);
  const theirs = opp ? sideState(opp, now) : null;
  const swaps = me.optimal_swaps.length > 0 || (opp?.optimal_swaps.length ?? 0) > 0;
  const grid = opp ? HALF_GRID : SOLO_GRID;
  return (
    <Card className={BOARD_CARD} aria-label="Lineups">
      <CardHeader className={`${grid} items-baseline pt-3 sm:pt-3.5`}>
        <div className="flex min-w-0 items-baseline gap-x-2.5">
          <h2 className="min-w-0 truncate font-display text-xl font-semibold uppercase tracking-[0.14em]">
            {data.mine !== false ? "My lineup" : <TeamName side={me} />}
          </h2>
          <span className={`${LABEL} shrink-0 whitespace-nowrap tabular-nums`}>{mine.label}</span>
        </div>
        <span />
        {opp && theirs && (
          <div className="flex min-w-0 items-baseline justify-end gap-x-2.5">
            <span className={`${LABEL} shrink-0 whitespace-nowrap tabular-nums`}>{theirs.label}</span>
            <h2 className="min-w-0 truncate font-display text-xl font-semibold uppercase tracking-[0.14em]">
              <TeamName side={opp} />
            </h2>
          </div>
        )}
      </CardHeader>
      <CardContent className="-mt-1 pb-1">
        <Group title={null} a={me.lineup} b={opp?.lineup ?? []} week={week} slot now={now} grid={grid} onPick={onPick} />
        <div className={`${grid} items-start`}>
          <SideTotals side={me} now={now} end="me" />
          <span />
          {opp && <SideTotals side={opp} now={now} end="opp" />}
        </div>
        <Group title="Bench" a={me.bench} b={opp?.bench ?? []} week={week} slot={false} now={now} grid={grid} onPick={onPick} />
        <Group title="IR" a={me.ir} b={opp?.ir ?? []} week={week} slot={false} now={now} grid={grid} onPick={onPick} />
      </CardContent>
      {swaps && (
        <CardFooter className={`${grid} items-start rounded-none border-t border-foreground/10 bg-transparent pt-3 pb-3`}>
          <Swaps side={me} end="me" />
          <span />
          {opp && <Swaps side={opp} end="opp" />}
        </CardFooter>
      )}
    </Card>
  );
}

// `now` is the clock the game phases are read against; the live wrapper advances it on each poll.
// The breakdown dialog holds the tapped player's id, not the player, so a live tick refreshes
// his numbers while it is open.
// `back` is the League scoreboard a game was opened from; my own Matchup page has none.
export function MatchupView({ data, now, back }: { data: Week; now: number; back?: string }) {
  const [picked, setPicked] = useState<{ id: number; end: End } | null>(null);
  const [open, setOpen] = useState(false);
  const roster = (s: Side | null) => (s ? [...s.lineup, ...s.bench, ...s.ir] : []);
  const player = picked ? roster(picked.end === "me" ? data.me : data.opp).find((p) => p.player_id === picked.id) ?? null : null;
  const onPick = (p: Player) => {
    setPicked({ id: p.player_id, end: roster(data.me).some((q) => q.player_id === p.player_id) ? "me" : "opp" });
    setOpen(true);
  };
  return (
    <div className="space-y-5">
      {back && (
        <Link
          href={back}
          className="-ml-1 -mb-2 inline-flex items-center gap-0.5 rounded-sm pr-1 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 outline-none transition-colors hover:text-foreground/85 focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          <ChevronLeft aria-hidden className="size-4" />
          Scoreboard
        </Link>
      )}
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
      <Board data={data} now={now} onPick={onPick} />
      <BoxScoreDialog p={player} open={open && player != null} onOpenChange={setOpen} />
    </div>
  );
}
