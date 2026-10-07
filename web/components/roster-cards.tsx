"use client";

import { ArrowLeftRight, ChevronDown, LoaderCircle, Lock, Plus } from "lucide-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Separator } from "@/components/ui/separator";
import type { Card } from "@/lib/api";
import { STATUS_SHORT, TONE } from "@/lib/board";
import { injuryLabel, injuryTone, pts, shortName } from "@/lib/format";
import { isMismatch, SLOT_ORDER, slotLabel, type LineupFailure } from "@/lib/lineup";
import { NAME_LINK } from "@/lib/player-log";
import { headshotUrl, team } from "@/lib/teams";
import { Kickoff } from "./local-time";
import { Outlook } from "./outlook";
import { PlayerTrigger } from "./player-dialog";
import { TeamLogo } from "./player-media";
import { StatLine } from "./stat-line";

const CALL = { START: "start", BENCH: "muted", OUT: "out", BYE: "muted", IR: "out" } as const;
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
// over the call chip, so the two right edges are one line down the list; the swap and the chevron are one pair at
// 44px standing across both lines. md+: one line; `body` and its two lines dissolve (display: contents) into fixed
// columns (matchup, number, call, actions) that line up row to row, and only the person column flexes. Below lg
// the slot reads BE for Bench, the name is shortened and the kickoff drops ":00", so a 768px tablet holds one line.
const GRID =
  "grid items-center gap-x-1.5 px-2 py-2 " +
  "grid-cols-[1.75rem_2.5rem_minmax(0,1fr)_5rem] " +
  "[grid-template-areas:'slot_shot_body_ctl'_'msg_msg_msg_msg'] " +
  "md:gap-x-2.5 md:py-2.5 md:grid-cols-[2.25rem_2.5rem_minmax(0,1fr)_10.5rem_3.5rem_9.5rem_5.5rem] " +
  "lg:gap-x-3 lg:grid-cols-[3.25rem_2.5rem_minmax(0,1fr)_12.5rem_4rem_9.5rem_4.5rem] " +
  "md:[grid-template-areas:'slot_shot_name_match_num_call_ctl'_'msg_msg_msg_msg_msg_msg_msg']";

const SLOT_TEXT =
  "font-display text-[0.75rem] font-semibold uppercase tracking-[0.06em] [grid-area:slot] md:text-[0.8125rem] lg:tracking-[0.12em]";

// The swap and the chevron: one pair, 44px each where a thumb uses them, 36px from lg.
const ACTION = "size-11 lg:size-9";
// On the phone the pair's column is 8px short of its two targets: they reach into the row's right padding, so the
// matchup line keeps that width and the chevron's target ends at the card's edge.
const PAIR = "flex items-center justify-end [grid-area:ctl] max-md:-mr-2";

// The slot as ESPN labels it; below lg the narrow column takes BE for Bench, ESPN's own slot code.
function SlotName({ slot }: { slot: string }) {
  const label = slotLabel(slot);
  if (slot !== "BE") return <>{label}</>;
  return <><span aria-hidden className="lg:hidden">BE</span><span className="max-lg:sr-only">{label}</span></>;
}

// The short kickoff, the way a scores app sets it in a narrow line: "Sun 1 PM", "Mon 8:15 PM" (minutes only
// when there are any), in the viewer's zone.
function ShortKickoff({ iso }: { iso: string }) {
  const d = new Date(iso);
  const opts: Intl.DateTimeFormatOptions = { weekday: "short", hour: "numeric", ...(d.getMinutes() ? { minute: "2-digit" } : {}) };
  return <span suppressHydrationWarning>{d.toLocaleString(undefined, opts)}</span>;
}

// "at LAR" when he travels, "vs SF" at home; without a venue the opponent still shows, bare. One line at every
// width: inline text on the phone, so the line ends in an ellipsis; from md the mark joins it. A game on shows its
// clock and score in the short form everywhere, since the full score does not fit the column.
function Matchup({ c }: { c: Card }) {
  const when = c.game || !c.kickoff
    ? <Kickoff iso={c.kickoff} bye={c.bye} game={c.game} team={c.team} compact />
    : <><span className="lg:hidden"><ShortKickoff iso={c.kickoff} /></span><span className="max-lg:hidden"><Kickoff iso={c.kickoff} bye={c.bye} /></span></>;
  if (!c.opponent) return <span className="text-xs text-muted-foreground md:text-sm">{when}</span>;
  return (
    // the phone line is set at the caption size, so the kickoff fits beside the call chip more often than not
    <span className="text-xs text-muted-foreground md:flex md:items-center md:gap-x-1.5 md:text-sm">
      {c.home !== null && <>
        <span className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-foreground/80 md:text-[0.8125rem]">{c.home ? "vs" : "at"}</span>{" "}
      </>}
      <span className="contents max-md:hidden"><TeamLogo abbr={c.opponent} size={18} /></span>
      <span className="font-medium text-foreground/85">{c.opponent}</span>{" "}
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

// The projection's sources under the reason: what the row's number is the mean of, and how far apart they are.
function Sources({ c, live }: { c: Card; live: boolean }) {
  return (
    <p className="text-xs tabular-nums text-muted-foreground">
      {live && <span className="mr-2">proj {pts(c.proj)}</span>}
      {c.espn_only
        ? <span>ESPN only</span>
        : <span>ESPN {pts(c.proj_espn)} <span aria-hidden>·</span> Sleeper {pts(c.proj_sleeper)}</span>}
      {c.spread != null && c.spread >= 3 && (
        <span className="ml-2 font-medium text-close-foreground dark:text-close">±{pts(c.spread)}</span>
      )}
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

// The call as one chip, with the accent ring when ESPN has him where the call would not. A close call is an amber
// mark inside it; with `split` (the roster row) it becomes its own CLOSE CALL chip from md, where the call column
// holds both. An IR or OUT call on an injured man carries his return week when `week` is given.
export function CallChip({ c, week, split = false }: { c: Card; week?: number; split?: boolean }) {
  const off = isMismatch(c);
  const text = callIsStatus(c) && week != null ? injuryLabel(c.call, c.return_week, week) : c.call;
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

export function Failure({ f, reload }: { f: LineupFailure; reload: () => void }) {
  return (
    <div role={f.moved ? "status" : "alert"} className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1 pt-1.5 text-sm [grid-area:msg]">
      <p className={f.moved ? "text-foreground/85" : "text-out"}>{f.text}</p>
      {f.reload && <Button variant="outline" size="sm" className="h-11 px-3.5 md:h-7 md:px-2.5" onClick={reload}>Reload</Button>}
    </div>
  );
}

function PlayerRow({ c, week, runBusy, open, onOpen, swap, notice }: {
  c: Card; week: number; runBusy: boolean; open: boolean; onOpen: (open: boolean) => void; swap: SwapControl;
  notice: string | null;
}) {
  const idle = c.call === "BYE" || c.call === "OUT" || c.call === "IR";
  const live = c.game_played > 0 || c.actual > 0;
  const big = live ? c.actual : c.proj;
  const picked = swap.picked?.player_id === c.player_id;
  const busy = swap.pending.includes(c.player_id);
  const anyBusy = swap.pending.length > 0;
  // while his sheet is open the sheet says it
  const error = swap.error?.id === c.player_id && !picked ? swap.error : null;
  const ir = c.slot === "IR" || c.group === "ir";
  const slot = slotLabel(c.slot);
  return (
    <li aria-busy={busy || undefined} className={picked ? "bg-foreground/[0.045]" : undefined}>
      <Collapsible open={open} onOpenChange={onOpen}>
        <div className={GRID}>
          <span className={`${SLOT_TEXT} ${c.slot === "BE" || ir ? "text-muted-foreground" : "text-foreground/85"}`}>
            <SlotName slot={c.slot} />
          </span>
          <div className="[grid-area:shot]"><Headshot c={c} idle={idle} /></div>
          <div className="flex min-w-0 flex-col gap-y-0.5 [grid-area:body] md:contents">
            {/* line 1: who, and the number flush right */}
            <div className="flex min-w-0 items-baseline justify-between gap-x-2 md:contents">
              <div className="min-w-0 md:[grid-area:name]">
                <p className={`flex items-baseline gap-1.5 font-display text-[1.0625rem] font-semibold leading-tight tracking-tight md:text-[1.1875rem] ${idle ? "text-foreground/70" : ""}`}>
                  <PlayerTrigger p={c} className={`min-w-0 truncate rounded-sm ${NAME_LINK}`}>
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
              <div className="shrink-0 text-right md:[grid-area:num]" title={live ? `${pts(c.actual)} points scored · projected ${pts(c.proj)}` : "projected points"}>
                <span className={`block font-display text-[1.5rem] font-bold leading-none tracking-tight tabular-nums ${!live && c.proj === 0 ? "text-muted-foreground" : ""}`}>
                  {pts(big)}
                </span>
                <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground max-md:sr-only">
                  {live ? "pts" : "proj"}
                </span>
              </div>
            </div>
            {/* line 2: vs/when, giving way to an ellipsis, and the call flush right under the number */}
            <div className="flex min-w-0 items-center justify-between gap-x-2 md:contents">
              {/* one 20px line, the chip's height: the mixed faces would otherwise stand the line box 24px tall */}
              <div className="min-w-0 truncate max-md:h-5 max-md:leading-5 md:[grid-area:match]">
                {/* the slot already says it for a QB in QB; a FLEX, bench or IR row needs it */}
                {c.pos !== slot && <><span className="text-xs font-semibold text-foreground/80 md:hidden">{c.pos}</span>{" "}</>}
                <Matchup c={c} />
              </div>
              <div className="flex shrink-0 gap-1 md:[grid-area:call]"><CallChip c={c} week={week} split /></div>
            </div>
          </div>
          <div className={PAIR}>
            {ir ? <span aria-hidden className={ACTION} /> : (
              <Button
                variant="ghost" size="icon"
                className={`-my-2.5 ${ACTION} aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:hover:bg-foreground/85 md:my-0`}
                aria-pressed={picked}
                aria-haspopup="dialog"
                aria-label={c.locked ? `${c.name}: his game has started` : `Move ${c.name}`}
                title={c.locked ? "his game has started" : undefined}
                disabled={c.locked || anyBusy}
                onClick={() => swap.pick(c)}
              >
                {busy
                  ? <LoaderCircle aria-hidden className="animate-spin motion-reduce:animate-none" />
                  : c.locked ? <Lock aria-hidden /> : <ArrowLeftRight aria-hidden />}
              </Button>
            )}
            <CollapsibleTrigger
              render={<Button variant="ghost" size="icon" className={`group/chev -my-2.5 ${ACTION} md:my-0`} />}
              aria-label={`${open ? "Hide" : "Show"} ${c.name}'s reason and outlook`}
            >
              <ChevronDown aria-hidden className="transition-transform duration-300 ease-(--ease-arrive) group-data-[panel-open]/chev:rotate-180" />
            </CollapsibleTrigger>
          </div>
          {error && <Failure f={error} reload={swap.reload} />}
          {notice && !error && <p aria-hidden className="pt-1 text-right text-xs text-muted-foreground [grid-area:msg]">{notice}</p>}
        </div>
        <CollapsibleContent className="h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-300 ease-(--ease-arrive) data-[ending-style]:h-0 data-[starting-style]:h-0">
          {/* under the person column: the slot, tile and their gutters plus the row's own padding */}
          <div className="space-y-2 px-2 pb-3 md:pl-[6.5rem] md:pr-[6.625rem] lg:pl-[7.75rem] lg:pr-[5.75rem]">
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

// A starting slot ESPN has nobody in. A tap opens the sheet of who can fill it; the swap sheet offers it too.
function EmptyRow({ slot, swap }: { slot: string; swap: SwapControl }) {
  return (
    <li>
      <button type="button" onClick={() => swap.fill(slot)} disabled={swap.pending.length > 0} aria-haspopup="dialog"
        aria-label={`Fill the empty ${slotLabel(slot)} slot`}
        className={`${GRID} group/empty w-full text-left outline-none transition-colors hover:bg-foreground/[0.04] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring active:bg-foreground/[0.07] disabled:pointer-events-none`}>
        <span className={`${SLOT_TEXT} text-foreground/85`}><SlotName slot={slot} /></span>
        <span aria-hidden className="size-10 rounded-md border border-dashed border-foreground/25 [grid-area:shot]" />
        <span className="font-display text-[1.0625rem] font-semibold leading-tight tracking-tight text-muted-foreground [grid-area:body] md:[grid-area:name] md:text-[1.1875rem]">
          Empty
        </span>
        {/* the plus stands where the swap does; the chevron's place stays empty */}
        <span aria-hidden className={PAIR}>
          <span className={`-my-2.5 flex ${ACTION} items-center justify-center rounded-md text-muted-foreground transition-colors group-hover/empty:text-foreground md:my-0`}>
            <Plus className="size-4" />
          </span>
          <span className={ACTION} />
        </span>
      </button>
    </li>
  );
}

type Item = { kind: "card"; c: Card } | { kind: "empty"; slot: string; key: string };
const order = (slot: string) => { const i = SLOT_ORDER.indexOf(slot); return i < 0 ? SLOT_ORDER.length : i; };

// Starters in ESPN's lineup order with its empty slots in place; the bench and IR as the API sends them.
function items(cards: Card[], group: Card["group"], empty: string[]): Item[] {
  const rows: Item[] = cards.filter((c) => c.group === group).map((c) => ({ kind: "card", c }));
  if (group !== "starter") return rows;
  rows.push(...empty.map((slot, i): Item => ({ kind: "empty", slot, key: `${slot}-${i}` })));
  const slotOf = (r: Item) => (r.kind === "card" ? r.c.slot : r.slot);
  return rows.map((r, i) => [r, i] as const).sort(([a, i], [b, j]) => order(slotOf(a)) - order(slotOf(b)) || i - j).map(([r]) => r);
}

export function RosterRows({ cards, empty, week, runBusy, open, setOpen, swap, notice }: {
  cards: Card[]; empty: string[]; week: number; runBusy: boolean;
  open: number | null; setOpen: (id: number | null) => void; swap: SwapControl; notice: Notice | null;
}) {
  return (
    <div className="space-y-7">
      {/* read out from here; shown beside the row that moved, which may be far down the page */}
      <p aria-live="polite" className="sr-only">{notice?.text}</p>
      {GROUPS.map(({ key, title }) => {
        const rows = items(cards, key, empty);
        if (rows.length === 0) return null;
        const filled = rows.filter((r) => r.kind === "card").length;
        return (
          <section key={key} className="space-y-3" aria-labelledby={`group-${key}`}>
            <h2 id={`group-${key}`} className="flex items-baseline gap-3">
              <span className="font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</span>
              <span className="font-display text-sm font-medium tabular-nums text-muted-foreground">
                {filled === rows.length ? filled : `${filled} of ${rows.length}`}
              </span>
              <Separator className="mb-1.5 flex-1 self-end bg-foreground/12" />
            </h2>
            <ul className="divide-y divide-foreground/10 overflow-hidden rounded-lg bg-card shadow-[0_10px_28px_-16px_rgb(0_0_0_/_0.55)]">
              {rows.map((r) => r.kind === "card"
                ? <PlayerRow key={r.c.player_id} c={r.c} week={week} runBusy={runBusy} swap={swap}
                    notice={notice?.id === r.c.player_id ? notice.text : null}
                    open={open === r.c.player_id} onOpen={(o) => setOpen(o ? r.c.player_id : null)} />
                : <EmptyRow key={r.key} slot={r.slot} swap={swap} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
