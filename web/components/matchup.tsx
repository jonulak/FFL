"use client";

import { ChevronLeft } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Item, ItemActions, ItemContent, ItemDescription, ItemFooter, ItemGroup, ItemTitle } from "@/components/ui/item";
import { Separator } from "@/components/ui/separator";
import type { Player, Side, Week } from "@/lib/api";
import { BOARD_CARD, HEALTHY, isIdle, LABEL, SLOT_LABEL, STATUS_SHORT, TONE } from "@/lib/board";
import { injuryTone, pts, record, shortName, signed } from "@/lib/format";
import { FIELD, fieldOf, inGameOf } from "@/lib/field";
import { gameOn, gamePhase, sideTotals, toPlay } from "@/lib/game";
import { finalP } from "@/lib/win-prob";
import { playsHref } from "@/lib/matchup-link";
import { pairStarters } from "@/lib/lineup";
import { gameLinkFor } from "@/lib/nfl";
import { clubLogoSrc } from "@/lib/teams";
import { ClubLogo } from "./club-logo";
import { BoxScoreDialog } from "./box-score-dialog";
import { fetchLog, holdFor, type LogFetch } from "./player-dialog";
import { GameLink } from "./game-link";
import { GameLine } from "./local-time";
import { PlayFeedStrip } from "./play-feed";
import { ProjectionTrigger, Spread } from "./projection";
import { StaleBadge } from "./stale-badge";
import { StatLine } from "./stat-line";
import { TeamLink } from "./team-link";
import { WinMeter } from "./win-meter";
import { LiveChip } from "./live-chip";
import { Presence, Swap, Ticker, useReorder } from "./motion";

// The board's grid, shared by every line so the slot column runs in one spine from the header to
// the footer: my half, the slot, the opponent's half.
const HALF_GRID = "grid grid-cols-[minmax(0,1fr)_1.75rem_minmax(0,1fr)] gap-x-1 sm:grid-cols-[minmax(0,1fr)_2.5rem_minmax(0,1fr)] sm:gap-x-3";
// A week with no opponent (a bye or an unpaired playoff week): my half and the spine, no empty right half.
const SOLO_GRID = "grid grid-cols-[minmax(0,1fr)_1.75rem] gap-x-1 sm:grid-cols-[minmax(0,1fr)_2.5rem] sm:gap-x-3";

type End = "me" | "opp";

// Where a side stands: nothing started yet, some starters still to play, or every game final.
// A side has started once any starter's game is on or any points have posted.
function sideState(side: Side, now: number) {
  const started = side.actual_total > 0 || side.lineup.some((p) => gamePhase(p, now) !== "pending");
  const left = toPlay(side.lineup, isIdle, now);
  return { started, left, label: !started ? "proj" : left > 0 ? `${left} to play` : "final" };
}

// The small uppercase unit on a numeral's baseline: PROJ for a forecast, PTS for a posted score.
function Unit({ fact, className = "" }: { fact: boolean; className?: string }) {
  return <span className={`font-display font-semibold uppercase tracking-[0.12em] text-muted-foreground ${className}`}><Swap>{fact ? "pts" : "proj"}</Swap></span>;
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
  // one team name wraps and the other does not. One shrinkable column: an auto one grows to the
  // widest unbreakable line and pushes the opponent's mark past the card's edge.
  return (
    <div className={`row-span-3 grid min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-subgrid gap-y-0 ${opp ? "text-right" : ""}`}>
      {/* Both marks hang from the top of the row with each name's cap line level with them, so the
          two sides start at one height whichever name wraps; the caption sits at the row's foot, so
          the records still read across the bug at one height. */}
      <div className={`flex items-start gap-2.5 sm:gap-3 ${opp ? "flex-row-reverse" : ""}`}>
        <ClubLogo src={clubLogoSrc(side)} abbrev={side.abbrev} size={40} className="sm:hidden" />
        <ClubLogo src={clubLogoSrc(side)} abbrev={side.abbrev} size={48} className="hidden sm:flex" />
        {/* the whole name, however many lines it takes: a long team name ("Anthony's Astounding Team")
            wraps to three at the phone's size rather than clip, and a word wider than the column hyphenates;
            the record and the manager's name sit under it as a caption, the record first in ink as on
            the League's tiles. `text-box` trims the name to cap height so its top edge is the ink's. */}
        <div className="flex min-w-0 flex-1 flex-col justify-between self-stretch">
          <p className="hyphens-auto text-balance font-display text-[1.125rem] font-semibold leading-[1.05] tracking-tight [overflow-wrap:anywhere] [text-box:trim-both_cap_alphabetic] sm:text-[1.65rem] sm:leading-none">
            <TeamLink id={side.team_id}>{side.team}</TeamLink>
          </p>
          <p className="mt-2 text-xs leading-tight text-muted-foreground [overflow-wrap:anywhere] sm:mt-2.5 sm:text-sm">
            <span className="font-medium tabular-nums text-foreground/80"><Swap>{record(side)}</Swap></span>
            {/* on the phone the name takes its own line rather than break between first and last */}
            {side.owner && <><span aria-hidden className="hidden sm:inline"> · </span><span className="mt-0.5 block sm:mt-0 sm:inline">{side.owner}</span></>}
          </p>
        </div>
      </div>
      {/* From `sm` the totals meet at VS, so the one comparison the bug exists for is one glance,
          not a jump across the card; the marks and names stay at the outer edges. */}
      <div className={`mt-2 flex items-baseline gap-1.5 ${opp ? "justify-end sm:justify-start" : "sm:justify-end"}`}>
        {/* the projection hands over to the score at the first kickoff (a crossfade); the score then counts up */}
        <Swap k={on ? "pts" : "proj"} className="font-display text-[2.6rem] font-bold leading-none tracking-tight sm:text-[3.25rem]"><Ticker value={big} format={pts} gain={on} /></Swap>
        <Unit fact={on} className="text-sm" />
      </div>
      {/* Two facts, each on its own line, so they never read as one sentence. */}
      <p className={`mt-2 min-h-4 text-xs tabular-nums text-muted-foreground ${opp ? "sm:text-left" : "sm:text-right"}`}>
        <Presence show={on}><span className="block whitespace-nowrap">proj {pts(side.proj_total)} <span aria-hidden>·</span> <Swap>{left > 0 ? `${left} to play` : "final"}</Swap></span></Presence>
        <Presence show={gain > 0.05}><span className="block"><span className="whitespace-nowrap">by projection <Ticker value={side.optimal_total} format={pts} /></span> <span className="whitespace-nowrap text-foreground/80">(<Ticker value={gain} format={signed} />)</span></span></Presence>
      </p>
    </div>
  );
}

// The whole game is over: both sides have started and neither has a starter left to play.
function boardFinal(data: Week, now: number) {
  return [data.me, data.opp].every((s) => {
    if (!s) return false;
    const { started, left } = sideState(s, now);
    return started && left === 0;
  });
}

function Scorebug({ data, now }: { data: Week; now: number }) {
  const over = boardFinal(data, now);
  // a final is read off the two scores as shown; before that, the model's odds
  const odds = data.opp && over ? finalP(data.me.actual_total, data.opp.actual_total) : data.win_prob?.me;
  return (
    <Card className={BOARD_CARD}>
      <CardContent className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] grid-rows-[auto_auto_auto_auto] gap-x-3 py-4 sm:gap-x-6 sm:py-5">
        <Total side={data.me} now={now} end="me" />
        <span aria-hidden className="row-span-3 self-center font-display text-sm font-semibold uppercase tracking-[0.14em] text-muted-foreground sm:text-base">vs</span>
        {data.opp
          ? <Total side={data.opp} now={now} end="opp" />
          : <p className="row-span-3 self-center text-right text-sm text-muted-foreground">No opponent this week</p>}
        {/* the odds under both totals, my share from the left; context beside the call, never the call */}
        {data.opp && (
          <Presence show={odds != null}>
            <div className="col-span-3 mt-4 border-t border-foreground/10 pt-3 sm:mt-5 sm:pt-4">
              <WinMeter p={odds ?? 0.5} teams={[data.me.team, data.opp.team]} final={over} />
            </div>
          </Presence>
        )}
      </CardContent>
    </Card>
  );
}

// The number, with no unit: on a Sunday afternoon the rows are a mix, and the sources' ± beside a
// forecast tells it from a posted score, which carries the projection it replaced beneath it and a
// live mark while the game is on. The numeral is 1.125rem on the phone, 1.375rem from `sm` with
// the note beneath.
function Num({ p, now, end }: { p: Player; now: number; end: End }) {
  const on = gamePhase(p, now) !== "pending";
  const opp = end === "opp";
  return (
    <div className={`tabular-nums ${opp ? "text-left" : "text-right"}`}>
      <div className={`flex items-baseline gap-1.5 ${opp ? "justify-start" : "justify-end"}`}>
        <Swap k={on ? "pts" : "proj"} className={`font-display text-[1.125rem] font-bold leading-none tracking-tight transition-colors duration-500 sm:text-[1.375rem] ${!on && p.proj === 0 ? "text-muted-foreground" : ""}`}>
          <Ticker value={on ? p.actual : p.proj} format={pts} gain={on} />
        </Swap>
        {/* the phone's game line has no room for it: the ± stands beside the number there */}
        {!on && <Spread p={p} className="text-[0.6875rem] sm:hidden" />}
      </div>
      <div className={`mt-0.5 flex max-sm:hidden ${opp ? "justify-start" : "justify-end"}`}><NumNote p={p} now={now} /></div>
    </div>
  );
}

// What stands under the number: before kickoff the sources' ± (amber from 3); the tap's box score lists the sources. Once his game is on, the projection it replaced, after a live dot. With
// his unit on the field the dot takes the half's wash colour (lib/field.ts): colour, not words.
function NumNote({ p, now }: { p: Player; now: number }) {
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
  const field = fieldOf(p);
  const dot = field === "red_zone" ? "bg-redzone" : field === "on" ? "bg-onfield" : "bg-foreground/75";
  return (
    <span className="flex shrink-0 items-center gap-x-1 whitespace-nowrap text-[0.6875rem] leading-4 tabular-nums text-muted-foreground sm:text-xs">
      <Presence show={phase === "live"}>
        <span className="flex items-center gap-x-1"><span aria-hidden className={`size-1.5 rounded-full transition-colors duration-700 ${dot}`} /><span className="max-sm:sr-only">live</span><span aria-hidden className="max-sm:hidden">·</span></span>
      </Presence>
      <Swap k={on ? "on" : "pending"}>
        {on
          ? <span>proj {pts(p.proj)}</span>
          : <Spread p={p} className="max-sm:hidden" />}
      </Swap>
    </span>
  );
}

// His injury status as a one- or two-letter chip in its tone, the full word for a screen reader.
function StatusTag({ p, className = "" }: { p: Player; className?: string }) {
  const status = p.injury.replaceAll("_", " ");
  return (
    <Presence show={!HEALTHY.has(p.injury)}>
      <span title={p.injury_note ?? status} className={`shrink-0 font-bold ${TONE[injuryTone(p.injury)]} ${className}`}>
        <span aria-hidden>{STATUS_SHORT[status] ?? status}</span>
        <span className="sr-only">{status.toLowerCase()}</span>
      </span>
    </Presence>
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
  const idle = isIdle(p);
  const phase = gamePhase(p, now);
  const on = phase !== "pending";
  // done for the week, idle or final: only the name steps back. The feed's "post" lands at the
  // whistle; ESPN's game_played (gamePhase's final) only some hours later.
  const done = idle || phase === "final" || p.game?.status === "post";
  // the wash: ink while his game is on, orange with his unit on the field, red in the red zone
  const field = inGameOf(p);
  // The phone half: the short name (`B. Purdy`) beside the number, then the game on one line
  // without his own club, `vs [DEN] Sun 4:25 PM` or `24-27 @ [CLE] Final`, his status tag (Q, DTD)
  // closing it (one line on a 390px phone, ffl-099), and once he has played the lead stat group
  // (passing for a QB, rushing for a RB, receiving for a WR) as the footer, with the whole half
  // to itself. From `sm` the full name with its one-letter status, the position before the game
  // line, the whole stat line. No return week: the board is this week's.
  // The half is a card with two taps: the projected number opens its sources (ProjectionTrigger,
  // stacked above), anywhere else the scoring breakdown, through a button laid over the whole half
  // (a button cannot hold the number's own button).
  return (
    <Item
      size="sm"
      data-end={end}
      data-field={field ?? undefined}
      className={`field-half relative min-w-0 cursor-pointer flex-wrap items-start gap-x-2 gap-y-1 rounded-md border-0 px-1 py-1 text-left transition-colors hover:bg-foreground/[0.04] has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-ring/60 sm:flex-nowrap sm:gap-3 sm:px-1.5 ${opp ? "-mr-1 flex-row-reverse sm:-mr-1.5" : "-ml-1 sm:-ml-1.5"}`}
    >
      <button type="button" onClick={() => onPick(p)} aria-label={`${p.name}${field ? `, ${FIELD[field].sr}` : ""}: scoring breakdown`}
        className="absolute inset-0 z-0 cursor-pointer rounded-md outline-none" />
      <ItemContent className={`min-w-0 gap-0 ${opp ? "items-end text-right" : ""}`}>
        <ItemTitle className={`line-clamp-none flex! max-w-full items-center gap-x-1.5 gap-y-0.5 flex-wrap font-display text-[1rem] font-semibold leading-none tracking-tight transition-colors duration-700 sm:gap-x-2 sm:text-[1.125rem] ${opp ? "flex-row-reverse" : ""} ${done ? "text-foreground/40" : ""}`}>
          {/* a name and its chip wrap rather than cut the name: `A. St. Brown BYE` is wider than a phone half */}
          <span className="min-w-0 break-words"><span className="sm:hidden">{shortName(p.name)}</span><span className="hidden sm:inline">{p.name}</span></span>
          {/* the phone's tag closes the game line */}
          <StatusTag p={p} className="max-sm:hidden" />
          <Presence show={p.bye}><Badge variant="muted" shape="chip">bye</Badge></Presence>
        </ItemTitle>
        {/* `max-sm:hidden!`: the `flex!` that beats shadcn's line-clamp reset would beat a plain `max-sm:hidden` too */}
        <ItemDescription className={`mt-2 line-clamp-none flex! flex-wrap items-center gap-x-1.5 text-xs leading-4 tabular-nums max-sm:hidden! ${opp ? "justify-end text-right" : ""}`}>
          <span className="font-semibold text-foreground/80">{p.pos}</span>
          {/* the bye says itself in the chip; the line keeps only a real game */}
          {!p.bye && <GameLink href={gameLinkFor(p, week)} className="-my-1 py-1"><GameLine p={p} size={14} /></GameLink>}
        </ItemDescription>
        {/* The row's one fact once he has played: the box-score line, in the play feed's register,
            wrapping at its groups. The phone has the lead group only, as the footer below. */}
        {p.stat_line && <StatLine text={p.stat_line} className="mt-1.5 text-xs max-sm:hidden" wrap />}
      </ItemContent>
      <ItemActions className="self-start">
        {/* a posted score is no button of its own: the tap passes through to the breakdown (the
            Ticker is positioned, so it would otherwise stand over the half's button and take it) */}
        {on
          ? <div className="pointer-events-none"><Num p={p} now={now} end={end} /></div>
          : (
            <ProjectionTrigger p={p} week={week} className="relative z-10 -m-1 block p-1">
              <Num p={p} now={now} end={end} />
            </ProjectionTrigger>
          )}
      </ItemActions>
      {/* The phone's game line spans the half; the opponent's is mirrored, his game at the outer
          edge. Once his game is on the score line
          needs the width, so the note (`proj 17.8`) closes the stat line beneath instead. The game
          chip overhangs its line by 4px (GameLink's -mx-1), so the gap is 10px to leave 6 showing. */}
      <ItemFooter className={`mt-1 gap-x-2.5 text-[0.6875rem] leading-4 text-muted-foreground sm:hidden ${opp ? "flex-row-reverse" : ""}`}>
        <span className={`flex min-w-0 flex-wrap items-center gap-x-1.5 ${opp ? "justify-end" : ""}`}>
          {!p.bye && <GameLink href={gameLinkFor(p, week)} className="-my-1 py-1"><GameLine p={p} size={13} compact className={opp ? "justify-end" : ""} /></GameLink>}
        </span>
        <Presence show={hurt}>
          <span data-status-tag title={p.injury_note ?? status}
            className={`shrink-0 font-bold ${TONE[injuryTone(p.injury)]}`}>
            <span aria-hidden>{STATUS_SHORT[status] ?? status}</span>
            <span className="sr-only">{status.toLowerCase()}</span>
          </span>
        </Presence>
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
function Pair({ a, b, slot, week, now, grid, onPick, k }: { a?: Player; b?: Player; slot: string | null; week: number; now: number; grid: string; onPick: Pick; k: string }) {
  return (
    <div role="listitem" data-key={k} className={`${grid} items-start py-1`}>
      {a ? <Half p={a} week={week} now={now} end="me" onPick={onPick} /> : <span />}
      <span className="self-center text-center font-display text-[0.6875rem] font-semibold uppercase tracking-[0.06em] text-muted-foreground sm:text-xs">{slot}</span>
      {grid === HALF_GRID && (b ? <Half p={b} week={week} now={now} end="opp" onPick={onPick} /> : <span />)}
    </div>
  );
}

// One board line before it is drawn: the slot code for the spine (null on bench and IR), my player, his.
type Row = [string | null, Player | undefined, Player | undefined];

// Bench and IR pair line for line by position in the list; the starters pair slot by slot (pairStarters).
function zip(a: Player[], b: Player[]): Row[] {
  const n = Math.max(a.length, b.length);
  return Array.from({ length: n }, (_, i) => [null, a[i], b[i]]);
}

function Group({ title, rows, week, now, grid, onPick }: { title: string | null; rows: Row[]; week: number; now: number; grid: string; onPick: Pick }) {
  const key = ([s, x, y]: Row, i: number) => String(x?.player_id ?? y?.player_id ?? `${s}-${i}`);
  // a lineup changed on ESPN: the rows slide to their new places rather than jump
  const list = useReorder<HTMLDivElement>(rows.map(key).join());
  if (rows.length === 0) return null;
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
      <ItemGroup ref={list} className="gap-0 divide-y divide-foreground/10">
        {rows.map((r, i) => (
          <Pair key={key(r, i)} k={key(r, i)} a={r[1]} b={r[2]} slot={r[0] && (SLOT_LABEL[r[0]] ?? r[0])} week={week} now={now} grid={grid} onPick={onPick} />
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
  // the points posted count up under a lime wash; what is left to play and the estimate only count
  const cell = (label: string, n: number, lead = false, gain = false) => (
    <div className={`whitespace-nowrap ${opp ? "mr-5 text-left last:mr-0 sm:mr-7" : "ml-5 text-right first:ml-0 sm:ml-7"}`}>
      <p className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">{label}</p>
      <p className={`mt-0.5 font-display text-[1.25rem] font-bold leading-none tracking-tight tabular-nums ${lead ? "" : "text-foreground/70"}`}><Ticker value={n} format={pts} gain={gain} /></p>
    </div>
  );
  return (
    <div className={`border-t-2 border-foreground/35 pt-3 pb-1 ${opp ? "text-left" : "text-right"}`} aria-label={`${side.team} totals`}>
      <div className={`hidden items-end sm:flex ${opp ? "justify-start" : "justify-end"}`}>
        {opp ? (
          <>{cell("Est. final", t.estimate, true)}{cell("To play", t.toCome)}{cell("Scored", t.scored, false, true)}</>
        ) : (
          <>{cell("Scored", t.scored, false, true)}{cell("To play", t.toCome)}{cell("Est. final", t.estimate, true)}</>
        )}
      </div>
      <div className="sm:hidden">
        <p className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Est. final</p>
        <p className="mt-0.5 font-display text-[1.25rem] font-bold leading-none tracking-tight tabular-nums"><Ticker value={t.estimate} format={pts} /></p>
        <p className="mt-1 text-xs tabular-nums text-muted-foreground">
          scored <Ticker value={t.scored} format={pts} /> <span aria-hidden>·</span> to play <Ticker value={t.toCome} format={pts} />
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
        By projection <Ticker value={side.optimal_total - side.proj_total} format={signed} />
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
      {/* a label strip over the slot rows: the board's header band (DESIGN.md, Header band) */}
      <CardHeader className={`${grid} items-baseline bg-foreground/[0.04] py-3 sm:py-3.5`}>
        {/* a side too narrow for its name and state drops the state to a second line, never cuts the name */}
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2.5">
          <h2 className="min-w-0 break-words font-display text-xl font-semibold uppercase tracking-[0.14em]">
            {data.mine !== false ? "My lineup" : <TeamName side={me} />}
          </h2>
          <span className={`${LABEL} shrink-0 whitespace-nowrap tabular-nums`}><Swap>{mine.label}</Swap></span>
        </div>
        <span />
        {opp && theirs && (
          <div className="flex min-w-0 flex-row-reverse flex-wrap items-baseline gap-x-2.5">
            <h2 className="min-w-0 break-words text-right font-display text-xl font-semibold uppercase tracking-[0.14em]">
              <TeamName side={opp} />
            </h2>
            <span className={`${LABEL} shrink-0 whitespace-nowrap tabular-nums`}><Swap>{theirs.label}</Swap></span>
          </div>
        )}
      </CardHeader>
      <CardContent className="-mt-1 pb-1">
        <Group title={null} rows={pairStarters(data.slots, me.lineup, opp?.lineup ?? [])} week={week} now={now} grid={grid} onPick={onPick} />
        <div className={`${grid} items-start`}>
          <SideTotals side={me} now={now} end="me" />
          <span />
          {opp && <SideTotals side={opp} now={now} end="opp" />}
        </div>
        <Group title="Bench" rows={zip(me.bench, opp?.bench ?? [])} week={week} now={now} grid={grid} onPick={onPick} />
        <Group title="IR" rows={zip(me.ir, opp?.ir ?? [])} week={week} now={now} grid={grid} onPick={onPick} />
      </CardContent>
      {/* the lineup by projection opens under the board when one differs, and folds away once set */}
      <Presence show={swaps} fold>
        <CardFooter className={`${grid} items-start rounded-none border-t border-foreground/10 bg-transparent pt-3 pb-3`}>
          <Swaps side={me} end="me" />
          <span />
          {opp && <Swaps side={opp} end="opp" />}
        </CardFooter>
      </Presence>
    </Card>
  );
}

// `now` is the clock the game phases are read against; the live wrapper advances it on each poll.
// The breakdown dialog holds the tapped player's id, not the player, so a live tick refreshes
// his numbers while it is open.
// `back` is the League scoreboard a game was opened from; my own Matchup page has none.
export function MatchupView({ data, now, back }: { data: Week; now: number; back?: string }) {
  const [picked, setPicked] = useState<{ id: number; end: End; log: LogFetch } | null>(null);
  const [open, setOpen] = useState(false);
  const latest = useRef(0);
  const roster = (s: Side | null) => (s ? [...s.lineup, ...s.bench, ...s.ir] : []);
  const player = picked ? roster(picked.end === "me" ? data.me : data.opp).find((p) => p.player_id === picked.id) ?? null : null;
  // The dialog carries his season log under the week, so a tap waits for it as the season modal
  // does (holdFor): opened first, the dialog grew under its own entrance and iOS drew a snap.
  const onPick = (p: Player) => {
    const tap = ++latest.current;
    const end: End = roster(data.me).some((q) => q.player_id === p.player_id) ? "me" : "opp";
    const log = fetchLog(p.player_id);
    void holdFor(log).then(() => {
      if (tap !== latest.current) return; // a later tap took over
      setPicked({ id: p.player_id, end, log });
      setOpen(true);
    });
  };
  // A play in the strip opens the same breakdown when its player is on this board (every play is
  // a starter's, but a lineup can change under a stale row); otherwise the season modal opens.
  const pickById = (id: number) => {
    const p = [...roster(data.me), ...roster(data.opp)].find((q) => q.player_id === id);
    if (p) onPick(p);
    return p != null;
  };
  return (
    <div className="space-y-5">
      {back && (
        <Link
          href={back}
          className={buttonVariants({ variant: "ghost", size: "sm", className: "-ml-2 -mb-2 gap-0.5 px-1 font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em] text-foreground/55 hover:bg-transparent dark:hover:bg-transparent hover:text-foreground/85" })}
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
        <LiveChip on={gameOn([...data.me.lineup, ...(data.opp?.lineup ?? [])])} />
        <StaleBadge since={data.stale_since} />
      </div>
      <Scorebug data={data} now={now} />
      {/* The feed only earns its place once something has happened; before kickoff it stays out of the way. */}
      <Presence show={data.plays.length > 0} fold>
        <div><PlayFeedStrip plays={data.plays} href={playsHref(data.mine !== false, data.me.team_id, data.week)} pick={pickById} /></div>
      </Presence>
      <Board data={data} now={now} onPick={onPick} />
      <BoxScoreDialog p={player} week={data.week} log={picked?.log ?? null} open={open && player != null} onOpenChange={setOpen} />
    </div>
  );
}
