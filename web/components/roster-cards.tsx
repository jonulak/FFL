"use client";

import { ChevronDown, LoaderCircle, Lock, Plus } from "lucide-react";
import type { Ref } from "react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import type { Card } from "@/lib/api";
import { STATUS_SHORT, TONE } from "@/lib/board";
import { injuryLabel, injuryTone, pts, shortName } from "@/lib/format";
import { irIllegal, isMismatch, SLOT_ORDER, slotLabel, type LineupFailure } from "@/lib/lineup";
import { gameLinkFor } from "@/lib/nfl";
import { NAME_LINK } from "@/lib/player-log";
import { headshotUrl, team } from "@/lib/teams";
import { Kickoff, useNow } from "./local-time";
import { Outlook, OutlookAge, outlookAgeLabel } from "./outlook";
import { PlayerTrigger } from "./player-dialog";
import { ProjectionTrigger, Spread } from "./projection";
import { TeamLogo } from "./player-media";
import { GameLink } from "./game-link";
import { StatLine } from "./stat-line";

// SIT is his own hand, not a verdict: muted like BENCH.
const CALL = { START: "start", BENCH: "muted", OUT: "out", BYE: "muted", IR: "out", SIT: "muted" } as const;
// ESPN says ACTIVE for a player and NORMAL for a defence; neither is news.
const HEALTHY = new Set(["ACTIVE", "NORMAL"]);
const GROUPS: { key: Card["group"]; title: string }[] = [
  { key: "starter", title: "Starters" }, { key: "bench", title: "Bench" }, { key: "ir", title: "IR" },
];

// A word on the last move, shown under the row of the player who moved.
export type Notice = { id: number; text: string };

// The swap control's state, owned by RosterLive: who is picked (his sheet is open), which players' move is saving,
// the last refusal; `pick` opens a player's sheet, `fill` an empty slot's.
export type SwapControl = {
  picked: Card | null;
  pending: number[];
  error: ({ id: number } & LineupFailure) | null;
  pick: (c: Card) => void;
  fill: (slot: string) => void;
  reload: () => void;
};

// One grid for every row, read slot -> who -> vs/when -> number -> call -> actions.
// Phone (< md): two lines in the `body` cell, the name over the matchup at the left and, flush right, the number
// over the call chip, so the two right edges are one line down the list; the chevron stands at 44px across both
// lines. The slot label is the move control, so there is no swap column. md+: one line; `body` and its two lines
// dissolve (display: contents) into fixed
// columns (matchup, number, call, actions) that line up row to row, and only the person column flexes. Below lg
// the slot reads BE for Bench, the name is shortened and the kickoff drops ":00", so a 768px tablet holds one line.
const GRID =
  "grid items-center gap-x-1.5 px-2 py-2 " +
  "grid-cols-[2.25rem_2.5rem_minmax(0,1fr)_2.25rem] " +
  "[grid-template-areas:'slot_shot_body_ctl'_'msg_msg_msg_msg'] " +
  "md:gap-x-2.5 md:py-2.5 md:grid-cols-[2.75rem_2.5rem_minmax(0,1fr)_10rem_4rem_9.5rem_2.75rem] " +
  "lg:gap-x-3 lg:grid-cols-[3.75rem_2.5rem_minmax(0,1fr)_12rem_4.5rem_9.5rem_2.25rem] " +
  "md:[grid-template-areas:'slot_shot_name_match_num_call_ctl'_'msg_msg_msg_msg_msg_msg_msg']";

const SLOT_TEXT =
  "font-display text-[0.75rem] font-semibold uppercase tracking-[0.06em] md:text-[0.8125rem] lg:tracking-[0.12em]";
// The slot's box, the same width on every row so the labels stand in one column, centred.
const SLOT_BOX = "flex h-7 w-full items-center justify-center rounded-md";
// The move control's face: an outline button in miniature (hairline ink, a faint fill), firmer on hover, ink on
// inverse while his sheet is open, as the Transactions page's pressed toggle. Focus rings the face, not the cell.
// A player out of the slot the call wants (isMismatch) gets the face in amber, the call chip's ring colour: the
// control that fixes it is the one that stands out.
const FACE_INK =
  "border-foreground/20 bg-foreground/[0.035] group-hover/slot:border-foreground/35 group-hover/slot:bg-foreground/[0.07] " +
  "group-active/slot:bg-foreground/[0.1]";
const FACE_OFF =
  "border-close bg-close/25 dark:bg-close/15 " +
  "group-hover/slot:bg-close/40 dark:group-hover/slot:bg-close/25 group-active/slot:bg-close/50";
const SLOT_FACE =
  `${SLOT_BOX} border transition-colors ` +
  "group-aria-pressed/slot:border-foreground group-aria-pressed/slot:bg-foreground group-aria-pressed/slot:text-background " +
  "group-focus-visible/slot:outline-2 group-focus-visible/slot:outline-offset-2 group-focus-visible/slot:outline-start";
// The cell reaches into the row's padding (left and both ends) so the target is 44px wide and the row's height.
const SLOT_CELL = "-my-2 -ml-2 flex items-center self-stretch pl-2 [grid-area:slot] md:-my-2.5";

// The chevron: 44px where a thumb uses it, 36px from lg.
const ACTION = "size-11 lg:size-9";
// On the phone its column is 8px short of the target: it reaches into the row's right padding, so the matchup line
// keeps that width and the target ends at the card's edge.
const CTL = "flex items-center justify-end [grid-area:ctl] max-md:-mr-2";

// The slot as ESPN labels it; below lg the narrow column takes BE for Bench, ESPN's own slot code.
function SlotName({ slot }: { slot: string }) {
  const label = slotLabel(slot);
  if (slot !== "BE") return <>{label}</>;
  return <><span aria-hidden className="lg:hidden">BE</span><span className="max-lg:sr-only">{label}</span></>;
}

// The slot label is the move control (ESPN's MOVE): a tap opens his sheet of moves, IR included. A started game
// makes it a plain label with a lock mark.
function SlotControl({ c, picked, busy, disabled, onPick }: {
  c: Card; picked: boolean; busy: boolean; disabled: boolean; onPick: () => void;
}) {
  const tone = c.slot === "BE" || c.slot === "IR" ? "text-muted-foreground" : "text-foreground/85";
  const off = isMismatch(c);
  if (c.locked) {
    return (
      <div className={SLOT_CELL} title="his game has started">
        <span className={`${SLOT_BOX} gap-0.5 ${SLOT_TEXT} ${tone}`}>
          <SlotName slot={c.slot} />
          <Lock aria-hidden className="size-2.5 shrink-0 opacity-70" />
          <span className="sr-only">, {c.name}: his game has started</span>
        </span>
      </div>
    );
  }
  return (
    <button
      type="button"
      className={`group/slot ${SLOT_CELL} outline-none disabled:pointer-events-none`}
      aria-pressed={picked}
      aria-haspopup="dialog"
      aria-label={picked ? `Cancel moving ${c.name}` : `Move ${c.name}${off ? ", not where the call has him" : ""}`}
      disabled={disabled}
      onClick={onPick}
    >
      <span className={`${SLOT_FACE} ${SLOT_TEXT} ${off ? `${FACE_OFF} text-close-foreground dark:text-close` : `${FACE_INK} ${tone}`} ${disabled && !busy ? "opacity-50" : ""}`}>
        {busy ? <LoaderCircle aria-hidden className="size-3.5 animate-spin motion-reduce:animate-none" /> : <SlotName slot={c.slot} />}
      </span>
    </button>
  );
}

// The short kickoff, the way a scores app sets it in a narrow line: "Sun 1 PM", "Mon 8:15 PM" (minutes only
// when there are any), in the viewer's zone.
function ShortKickoff({ iso }: { iso: string }) {
  const d = new Date(iso);
  const opts: Intl.DateTimeFormatOptions = { weekday: "short", hour: "numeric", ...(d.getMinutes() ? { minute: "2-digit" } : {}) };
  return <span suppressHydrationWarning>{d.toLocaleString(undefined, opts)}</span>;
}

// "at LAR" when he travels, "vs SF" at home; without a venue the opponent still shows, bare. On the phone it is
// inline text in two unbreakable pieces, the opponent and the time, so a narrow row breaks between them
// (`at JAX` / `17-19 Final`) rather than cutting the score; from md the mark joins it on one line. A game on shows
// its clock and score in the short form everywhere, since the full score does not fit the column.
function Matchup({ c }: { c: Card }) {
  const when = c.game || !c.kickoff
    ? <Kickoff iso={c.kickoff} bye={c.bye} game={c.game} team={c.team} compact />
    : <><span className="whitespace-nowrap lg:hidden"><ShortKickoff iso={c.kickoff} /></span><span className="max-lg:hidden"><Kickoff iso={c.kickoff} bye={c.bye} /></span></>;
  if (!c.opponent) return <span className="text-xs text-muted-foreground md:text-sm">{when}</span>;
  return (
    // the phone line is set at the caption size, so the kickoff fits beside the call chip more often than not
    <span className="text-xs text-muted-foreground md:flex md:items-center md:gap-x-1.5 md:text-sm">
      <span className="whitespace-nowrap md:contents">
        {c.home !== null && <>
          <span className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-foreground/80 md:text-[0.8125rem]">{c.home ? "vs" : "at"}</span>{" "}
        </>}
        <span className="contents max-md:hidden"><TeamLogo abbr={c.opponent} size={18} /></span>
        <span className="font-medium text-foreground/85">{c.opponent}</span>
      </span>{" "}
      <span aria-hidden className="max-lg:hidden">·</span>
      <span className="md:min-w-0 md:truncate">{when}</span>
    </span>
  );
}

// The rule that fired. season.roster_cards appends " — his status is your call" for QUESTIONABLE/DOUBTFUL;
// that note gets its own small line.
function Reason({ text }: { text: string }) {
  const [main, note] = text.split(" — ", 2);
  return (
    <div>
      <p className="text-[0.9375rem] font-medium leading-snug text-pretty">{main}</p>
      {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

// Under the reason once his game is on (the number above is then his points): the projection it replaced.
function Sources({ c, live }: { c: Card; live: boolean }) {
  if (!live) return null;
  return (
    <p className="text-xs tabular-nums text-muted-foreground">
      proj {pts(c.proj)} <Spread p={c} />
    </p>
  );
}

// His portrait on a quiet tile, the club's colour as its left edge; a defence or a missing portrait shows the mark.
export function Headshot({ c, idle }: { c: Card; idle: boolean }) {
  const src = headshotUrl(c.player_id);
  return (
    <Avatar
      className={`size-10 overflow-hidden rounded-md border-l-[3px] bg-foreground/[0.07] after:rounded-md ${idle ? "opacity-70 saturate-[.35]" : ""}`}
      style={{ borderLeftColor: team(c.team).primary }}
    >
      {src && <AvatarImage src={src} alt="" className="rounded-none object-cover object-top" />}
      <AvatarFallback className="rounded-none bg-transparent"><TeamLogo abbr={c.team} size={26} /></AvatarFallback>
    </Avatar>
  );
}

const hurt = (c: Card) => !HEALTHY.has(c.injury);
// An IR or OUT call on an injured man is his status: the chip says it, with the return week.
const callIsStatus = (c: Card) => hurt(c) && (c.call === "IR" || c.call === "OUT");

// His injury status as ESPN's own letter after the name (Q, D, O, SUSP), in its colour and without a chip, at every
// width, as the Matchup board sets it; the full status and the return week are read out and in the title. Left out
// where the call chip already says it (IR on injury reserve, OUT on out).
export function InjuryMark({ c, week }: { c: Card; week?: number }) {
  if (!hurt(c)) return null;
  if ((c.call === "IR" && c.injury === "INJURY_RESERVE") || (c.call === "OUT" && c.injury === "OUT")) return null;
  const status = c.injury.replaceAll("_", " ");
  const back = week != null && c.return_week != null && c.return_week > week ? `, back week ${c.return_week}` : "";
  return (
    <span title={c.injury_note ?? `${status.toLowerCase()}${back}`} className={`shrink-0 text-[0.9375rem] font-bold ${TONE[injuryTone(c.injury)]}`}>
      <span aria-hidden>{STATUS_SHORT[status] ?? status}</span>
      <span className="sr-only">{status.toLowerCase()}{back}</span>
    </span>
  );
}

// The call as one chip, with the accent ring when ESPN has him where the call would not. A START names the slot the
// call puts him in (RB, FLEX, ...; STRATEGY.md, Slot by kickoff), still on the lime. A close call is an amber
// mark inside it; with `split` (the roster row) it becomes its own CLOSE CALL chip from md, where the call column
// holds both. An IR or OUT call on an injured man carries his return week when `week` is given.
export function CallChip({ c, week, split = false }: { c: Card; week?: number; split?: boolean }) {
  const off = isMismatch(c);
  const text = callIsStatus(c) && week != null ? injuryLabel(c.call, c.return_week, week)
    : c.call === "START" && c.call_slot ? <><span className="sr-only">start at </span>{slotLabel(c.call_slot)}</> : c.call;
  return (
    <>
      <Badge variant={CALL[c.call]} shape="chip" title={c.close_call ? "close call" : undefined}
        className={`${off ? "ring-2 ring-close/70" : ""} ${c.close_call ? (split ? "max-md:pr-0" : "pr-0") : ""}`}>
        {text}
        {off && <span className="sr-only">, differs from ESPN</span>}
        {/* the chip's trailing end in amber, cut off by a hairline: the call and its closeness as one block */}
        {c.close_call && (
          <span className={`-my-0.5 flex self-stretch ${split ? "md:hidden" : ""}`}>
            <span aria-hidden className="w-1.5 border-l border-close-foreground/40 bg-close" />
            <span className="sr-only">, close call</span>
          </span>
        )}
      </Badge>
      {split && c.close_call && <Badge variant="close" shape="chip" className="max-md:hidden">close call</Badge>}
    </>
  );
}

// An IR player with no injury designation (lineup.irIllegal): ESPN refuses every other move until he is off IR. Said
// above the lineup and in every other player's sheet, with the one button that opens his.
export function IrBlocked({ c, onFix, disabled }: { c: Card; onFix: () => void; disabled: boolean }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-x-4 gap-y-2.5 rounded-lg border border-out/45 bg-out/[0.08] px-3.5 py-3">
      <p className="min-w-0 flex-1 basis-56 text-[0.9375rem] leading-snug text-pretty">
        <span className="font-semibold">{c.name}</span> is on IR with no injury designation — ESPN blocks every move until he comes off IR.
      </p>
      <Button variant="outline" className="h-11 shrink-0 px-4 md:h-9" disabled={disabled || c.locked} onClick={onFix}>
        Move {shortName(c.name)}
      </Button>
    </div>
  );
}

export function Failure({ f, reload }: { f: LineupFailure; reload: () => void }) {
  return (
    <div role={f.moved ? "status" : "alert"} className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1 pt-1.5 text-sm [grid-area:msg]">
      <p className={f.moved ? "text-foreground/85" : "text-out"}>{f.text}</p>
      {f.reload && <Button variant="outline" size="sm" className="h-11 px-3.5 md:h-7 md:px-2.5" onClick={reload}>Reload</Button>}
    </div>
  );
}

function PlayerRow({ c, week, runBusy, open, onOpen, swap, notice, illegal }: {
  c: Card; week: number; runBusy: boolean; open: boolean; onOpen: (open: boolean) => void; swap: SwapControl;
  notice: string | null; illegal: boolean;
}) {
  const idle = c.call === "BYE" || c.call === "OUT" || c.call === "IR" || c.call === "SIT";
  const live = c.game_played > 0 || c.actual > 0;
  const big = live ? c.actual : c.proj;
  const picked = swap.picked?.player_id === c.player_id;
  const busy = swap.pending.includes(c.player_id);
  const anyBusy = swap.pending.length > 0;
  // while his sheet is open the sheet says it
  const error = swap.error?.id === c.player_id && !picked ? swap.error : null;
  const slot = slotLabel(c.slot);
  const now = useNow();
  return (
    <li data-flip={`p${c.player_id}`} data-slot={c.slot} aria-busy={busy || undefined}
      className={picked ? "bg-foreground/[0.045]" : isMismatch(c) ? "bg-close/[0.08] dark:bg-close/[0.06]" : undefined}>
      <Collapsible open={open} onOpenChange={onOpen}>
        <div className={GRID}>
          <SlotControl c={c} picked={picked} busy={busy} disabled={anyBusy} onPick={() => swap.pick(c)} />
          <div className="[grid-area:shot]"><Headshot c={c} idle={idle} /></div>
          <div className="flex min-w-0 flex-col gap-y-0.5 [grid-area:body] md:contents">
            {/* line 1: who, and the number flush right */}
            <div className="flex min-w-0 items-baseline justify-between gap-x-2 md:contents">
              <div className="min-w-0 md:[grid-area:name]">
                <p className={`flex flex-wrap items-baseline gap-x-1.5 font-display text-[1.0625rem] font-semibold leading-tight tracking-tight md:text-[1.1875rem] ${idle ? "text-foreground/70" : ""}`}>
                  <PlayerTrigger p={c} className={`min-w-0 break-words text-left rounded-sm ${NAME_LINK}`}>
                    <span className="lg:hidden">{shortName(c.name)}</span>
                    <span className="max-lg:hidden">{c.name}</span>
                  </PlayerTrigger>
                  <InjuryMark c={c} week={week} />
                </p>
                <p className="truncate text-xs text-muted-foreground max-md:hidden">
                  <span className="font-semibold text-foreground/80">{c.pos}</span>
                  <span aria-hidden> · </span>
                  {team(c.team).name || c.team}
                </p>
              </div>
              {/* a tap opens the sources; before kickoff the spread stands beside the number on the phone, under it from md */}
              <ProjectionTrigger p={c} week={week} className="block shrink-0 text-right md:[grid-area:num]"
                label={live ? `${c.name}: ${pts(c.actual)} points scored, projected ${pts(c.proj)}; show the sources` : undefined}>
                <span className="flex items-baseline justify-end gap-1">
                  <span className={`font-display text-[1.5rem] font-bold leading-none tracking-tight tabular-nums ${!live && c.proj === 0 ? "text-muted-foreground" : ""}`}>
                    {pts(big)}
                  </span>
                  {/* a fixed slot on every row, spread or not (a bye, a game on), so the numbers keep one right edge */}
                  <span className="w-[1.875rem] shrink-0 whitespace-nowrap text-left text-xs md:hidden">{!live && <Spread p={c} />}</span>
                </span>
                <span className="block whitespace-nowrap font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground max-md:sr-only">
                  {live ? "pts" : "proj"}
                  {!live && <Spread p={c} className="ml-1 normal-case tracking-normal" />}
                </span>
              </ProjectionTrigger>
            </div>
            {/* line 2: vs/when, breaking to a second line when the row is narrow, and the call flush right under the number */}
            <div className="flex min-w-0 items-center justify-between gap-x-2 md:contents">
              {/* at least the chip's 20px; the 16px line box keeps the mixed faces from standing it 24px tall,
                  and a wrapped line stays tight */}
              <div className="flex min-w-0 items-center max-md:min-h-5 max-md:leading-4 md:[grid-area:match]">
                {/* the slot already says it for a QB in QB; a FLEX, bench or IR row needs it */}
                {c.pos !== slot && <span className="mr-1 shrink-0 self-start text-xs font-semibold leading-5 text-foreground/80 md:hidden">{c.pos}</span>}
                {/* no `max-w-full` on the phone: the div is sized to the chip less its negative margin, so a
                    100% cap left the chip 4px short of its own line, which broke (and, truncated, cut "Final" to
                    "F…" at every phone width). As a min-w-0 flex item the chip still shrinks when it must. */}
                <GameLink href={gameLinkFor(c, week)} className="md:max-w-full"><span className="block min-w-0 md:truncate"><Matchup c={c} /></span></GameLink>
              </div>
              <div className="flex shrink-0 gap-1 md:[grid-area:call]"><CallChip c={c} week={week} split /></div>
            </div>
          </div>
          <div className={CTL}>
            <CollapsibleTrigger
              render={<Button variant="ghost" size="icon" className={`group/chev -my-2.5 flex-col gap-0.5 ${ACTION} md:my-0`} />}
              aria-label={`${open ? "Hide" : "Show"} ${c.name}'s reason and outlook${outlookAgeLabel(c, now)}`}
            >
              <ChevronDown aria-hidden className="transition-transform duration-300 ease-(--ease-arrive) group-data-[panel-open]/chev:rotate-180" />
              {/* the outlook's age, so the collapsed list says which to recheck */}
              <OutlookAge c={c} now={now} />
            </CollapsibleTrigger>
          </div>
          {error && <Failure f={error} reload={swap.reload} />}
          {notice && !error && <p aria-hidden className="pt-1 text-right text-xs text-muted-foreground [grid-area:msg]">{notice}</p>}
          {illegal && !error && !notice && (
            <p className="pt-1 text-right text-xs font-medium text-out [grid-area:msg]">No injury designation — move him off IR</p>
          )}
        </div>
        <CollapsibleContent className="h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-300 ease-(--ease-arrive) data-[ending-style]:h-0 data-[starting-style]:h-0">
          {/* under the person column: the slot, tile and their gutters plus the row's own padding */}
          <div className="space-y-2 px-2 pb-3 md:pl-[7rem] md:pr-[3.875rem] lg:pl-[8.25rem] lg:pr-[3.5rem]">
            <Reason text={c.reason} />
            <Sources c={c} live={live} />
            {c.stat_line && <StatLine text={c.stat_line} />}
            <div className="border-t border-foreground/10 pt-2"><Outlook c={c} runBusy={runBusy} /></div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

// A starting or IR slot ESPN has nobody in. A tap opens the sheet of who can fill it; the swap sheet offers it too.
function EmptyRow({ slot, flip, swap }: { slot: string; flip: string; swap: SwapControl }) {
  return (
    <li data-flip={`e${flip}`} data-slot={slot}>
      <button type="button" onClick={() => swap.fill(slot)} disabled={swap.pending.length > 0} aria-haspopup="dialog"
        aria-label={`Fill the empty ${slotLabel(slot)} slot`}
        className={`${GRID} group/empty w-full text-left outline-none transition-colors hover:bg-foreground/[0.04] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring active:bg-foreground/[0.07] disabled:pointer-events-none`}>
        {/* the slot in the move control's shape, dashed like the empty tile: the whole row is the Fill button */}
        <span className={SLOT_CELL}>
          <span className={`${SLOT_BOX} border border-dashed border-foreground/30 transition-colors group-hover/empty:border-foreground/50 ${SLOT_TEXT} text-foreground/85`}>
            <SlotName slot={slot} />
          </span>
        </span>
        <span aria-hidden className="size-10 rounded-md border border-dashed border-foreground/25 [grid-area:shot]" />
        <span className="font-display text-[1.0625rem] font-semibold leading-tight tracking-tight text-muted-foreground [grid-area:body] md:[grid-area:name] md:text-[1.1875rem]">
          Empty
        </span>
        {/* the plus stands in the chevron's place */}
        <span aria-hidden className={CTL}>
          <span className={`-my-2.5 flex ${ACTION} items-center justify-center rounded-md text-muted-foreground transition-colors group-hover/empty:text-foreground md:my-0`}>
            <Plus className="size-4" />
          </span>
        </span>
      </button>
    </li>
  );
}

type Item = { kind: "card"; c: Card } | { kind: "empty"; slot: string; key: string };
const order = (slot: string) => { const i = SLOT_ORDER.indexOf(slot); return i < 0 ? SLOT_ORDER.length : i; };

// Starters in ESPN's lineup order with its empty slots in place; the bench as the API sends it; IR with its open
// spots after its players.
function items(cards: Card[], group: Card["group"], empty: string[]): Item[] {
  const rows: Item[] = cards.filter((c) => c.group === group).map((c) => ({ kind: "card", c }));
  const open = (slots: string[]) => slots.map((slot, i): Item => ({ kind: "empty", slot, key: `${slot}-${i}` }));
  if (group === "ir") return [...rows, ...open(empty.filter((s) => s === "IR"))];
  if (group !== "starter") return rows;
  rows.push(...open(empty.filter((s) => s !== "IR")));
  const slotOf = (r: Item) => (r.kind === "card" ? r.c.slot : r.slot);
  return rows.map((r, i) => [r, i] as const).sort(([a, i], [b, j]) => order(slotOf(a)) - order(slotOf(b)) || i - j).map(([r]) => r);
}

export function RosterRows({ cards, empty, week, runBusy, open, setOpen, swap, notice, rootRef }: {
  cards: Card[]; empty: string[]; week: number; runBusy: boolean;
  open: number | null; setOpen: (id: number | null) => void; swap: SwapControl; notice: Notice | null;
  // the board's root, for RosterLive's move animation (use-flip.ts); the groups, cards and rows carry its marks
  rootRef?: Ref<HTMLDivElement>;
}) {
  const bad = irIllegal(cards);
  return (
    <div ref={rootRef} className="space-y-7">
      {/* read out from here; shown beside the row that moved, which may be far down the page */}
      <p aria-live="polite" className="sr-only">{notice?.text}</p>
      {bad && <IrBlocked c={bad} onFix={() => swap.pick(bad)} disabled={swap.pending.length > 0} />}
      {GROUPS.map(({ key, title }) => {
        const rows = items(cards, key, empty);
        if (rows.length === 0) return null;
        const filled = rows.filter((r) => r.kind === "card").length;
        return (
          <section key={key} data-flip-group={key} className="space-y-3" aria-labelledby={`group-${key}`}>
            <h2 id={`group-${key}`} className="flex items-baseline gap-3">
              <span className="font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</span>
              <span className="font-display text-sm font-medium tabular-nums text-muted-foreground">
                {filled === rows.length ? filled : `${filled} of ${rows.length}`}
              </span>
              <Separator className="mb-1.5 flex-1 self-end bg-foreground/12" />
            </h2>
            <ul data-flip-list={key} className="divide-y divide-foreground/10 overflow-hidden rounded-lg bg-card shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)]">
              {rows.map((r) => r.kind === "card"
                ? <PlayerRow key={r.c.player_id} c={r.c} week={week} runBusy={runBusy} swap={swap}
                    notice={notice?.id === r.c.player_id ? notice.text : null} illegal={bad?.player_id === r.c.player_id}
                    open={open === r.c.player_id} onOpen={(o) => setOpen(o ? r.c.player_id : null)} />
                : <EmptyRow key={r.key} flip={r.key} slot={r.slot} swap={swap} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
