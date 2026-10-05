"use client";

import { ChevronDown } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { clientGet } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { OppRank, PlayerLog, SeasonLog, SeasonTotals, WeekLog } from "@/lib/api";
import { ordinal, pts } from "@/lib/format";
import { columns, ptsText, seasonPts, weekLabel } from "@/lib/player-log";
import { BoxTable } from "./box-table";
import { BAND_CLOSE, PlayerHeader, type PlayerSeed } from "./player-header";
import { TeamLogo } from "./player-media";
import { StaleBadge } from "./stale-badge";

// The season modal: tap a player anywhere outside a matchup view and his game log opens over the
// page. One dialog for the whole app, mounted in the layout; any row opens it through
// PlayerTrigger with what it already knows, so the header draws before the log arrives.

const Open = createContext<(p: PlayerSeed) => void>(() => {});

// How long a tap waits for the log before opening the dialog without it. The log usually lands
// in well under this (about 60ms on the phone over the tailnet), and then the dialog opens at its
// full size. Opened first, it grew while its own entrance was still running, and iOS Safari drew
// that as a snap from one size to the other. A slower log opens on the skeleton and grows.
const HOLD_MS = 180;

type Fetch = { promise: Promise<PlayerLog>; log?: PlayerLog };
type Shown = { seed: PlayerSeed; fetch: Fetch };

export function PlayerDialogProvider({ children }: { children: React.ReactNode }) {
  const [shown, setShown] = useState<Shown | null>(null);
  const [open, setOpen] = useState(false);
  const latest = useRef(0);
  const show = useCallback((p: PlayerSeed) => {
    const tap = ++latest.current;
    const fetch: Fetch = { promise: clientGet<PlayerLog>(`/player/${p.player_id}`) };
    // registered before the race below, so `log` is set by the time the race settles
    fetch.promise.then((log) => { fetch.log = log; }, () => {});
    const hold = new Promise((r) => setTimeout(r, HOLD_MS));
    void Promise.race([fetch.promise.catch(() => {}), hold]).then(() => {
      if (tap !== latest.current) return; // a later tap took over
      setShown({ seed: p, fetch });
      setOpen(true);
    });
  }, []);
  return (
    <Open.Provider value={show}>
      {children}
      <Dialog open={open && shown != null} onOpenChange={setOpen}>
        <DialogContent className={`grid max-h-[85dvh] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-lg ${BAND_CLOSE}`}>
          {shown && <Body key={shown.seed.player_id} seed={shown.seed} fetch={shown.fetch} />}
        </DialogContent>
      </Dialog>
    </Open.Provider>
  );
}

// The tap target: a row's tile and name, never the whole row, so the row's own controls (the
// compare chevron, the outlook button, Rewrite) keep their clicks.
export function PlayerTrigger({ p, className = "", children }: { p: PlayerSeed; className?: string; children: React.ReactNode }) {
  const show = useContext(Open);
  return (
    <button type="button" onClick={() => show(p)} aria-label={`${p.name}: game log`}
      className={`cursor-pointer text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${className}`}>
      {children}
    </button>
  );
}

type Load = { state: "loading" } | { state: "ready"; log: PlayerLog } | { state: "error" };

// The log the tap already asked for; a retry asks again.
function Body({ seed, fetch }: { seed: PlayerSeed; fetch: Fetch }) {
  const [load, setLoad] = useState<Load>(() => fetch.log ? { state: "ready", log: fetch.log } : { state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (attempt === 0 && fetch.log) return;
    let current = true;
    (attempt === 0 ? fetch.promise : clientGet<PlayerLog>(`/player/${seed.player_id}`))
      .then((log) => current && setLoad({ state: "ready", log }))
      .catch(() => current && setLoad({ state: "error" }));
    return () => { current = false; };
  }, [seed.player_id, fetch, attempt]);
  const retry = () => { setLoad({ state: "loading" }); setAttempt((n) => n + 1); };
  const grow = useGrow(load.state);
  return (
    <>
      <PlayerHeader p={seed} week={load.state === "ready" ? load.log.current_week : undefined} />
      <div className="overflow-y-auto overscroll-contain">
        <div ref={grow} className="overflow-hidden px-4 pt-4 pb-5">
          {load.state === "loading" && <Loading />}
          {load.state === "error" && (
            <div className="log-in py-2">
              <p className="text-sm text-muted-foreground">ESPN&apos;s game log didn&apos;t load.</p>
              <Button size="sm" variant="outline" className="mt-2.5" onClick={retry}>Retry</Button>
            </div>
          )}
          {load.state === "ready" && <Seasons log={load.log} />}
        </div>
      </div>
    </>
  );
}

const GROW_MS = 420;
// Even in and out, not the arrive curve: the dialog is centred, so its top edge travels half the
// growth, and an ease-out spends nine tenths of that in the first 40ms, which reads as the modal
// opening off-centre and jumping into place.
const GROW_EASE = "cubic-bezier(0.65, 0, 0.35, 1)";

// The body's height follows the content through a change of state (skeleton to log, log to
// error, retry): measured before and after the swap and animated between, so the dialog grows
// from where it stood instead of snapping taller around its centre. Rows opening inside the log
// change no state and keep their own animation (a week's panel, a past season's fold).
function useGrow(state: Load["state"]) {
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const to = el.offsetHeight;
    const from = last.current;
    last.current = to;
    if (!from || from === to || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    el.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: GROW_MS, easing: GROW_EASE });
  }, [state]);
  return ref;
}

// The skeleton is the log's own shape (a section heading with its hairline, a table head, the
// week rows at their height), so the swap reads as the log filling in. It waits a beat before
// showing (log-wait): a quick answer never flashes grey.
function Loading() {
  return (
    <div role="status" className="log-wait space-y-1.5">
      <span className="sr-only">Loading the game log</span>
      <div className="flex items-end gap-3 pb-1">
        <Skeleton className="h-5 w-14" />
        <Skeleton className="h-3.5 w-24" />
        <Separator className="mb-1.5 flex-1 bg-foreground/12" />
      </div>
      <Skeleton className="h-3 w-full opacity-60" />
      <div className="divide-y divide-foreground/10">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="flex items-center gap-3 py-2.5">
            <Skeleton className="h-4 w-8" />
            <Skeleton className="h-4 w-14" />
            <Skeleton className="ml-auto h-4 w-1/3" />
            <Skeleton className="h-5 w-10" />
          </div>
        ))}
      </div>
    </div>
  );
}

// This season's weeks, then each past season folded into one totals row that opens onto its weeks.
function Seasons({ log }: { log: PlayerLog }) {
  const now = log.seasons.find((s) => s.season === log.season);
  const past = log.seasons.filter((s) => s.season < log.season && s.totals);
  const played = seasonPts(now?.weeks ?? []);
  return (
    <div className="space-y-7">
      <section aria-labelledby="season-now" className="log-in space-y-1.5">
        <Heading id="season-now" title={String(log.season)} stale={log.stale_since}
          note={played.games > 0 ? `${played.games} ${played.games === 1 ? "game" : "games"} · ${pts(played.pts)} pts` : undefined} />
        {now && now.weeks.length > 0
          ? <Weeks s={now} pos={log.pos} week={log.current_week} />
          : <p className="py-1 text-sm text-muted-foreground">No games yet.</p>}
      </section>
      <section aria-labelledby="season-past" className="log-in space-y-1.5" style={{ "--i": Math.min(now?.weeks.length ?? 0, 10) + 1 } as React.CSSProperties}>
        <Heading id="season-past" title="Past seasons" />
        {past.length > 0
          ? <PastSeasons seasons={past} />
          : <p className="py-1 text-sm text-muted-foreground">No games before {log.season}.</p>}
      </section>
    </div>
  );
}

// A section heading as the Roster page sets its groups: the title in the board face, its count
// beside it, the hairline running out to the right.
function Heading({ id, title, note, stale }: { id: string; title: string; note?: string; stale?: string | null }) {
  return (
    <h3 id={id} className="flex items-baseline gap-3">
      <span className="shrink-0 font-display text-xl font-semibold uppercase tracking-[0.14em]">{title}</span>
      {note && <span className="font-display text-sm font-medium tabular-nums text-muted-foreground">{note}</span>}
      {stale && <StaleBadge since={stale} />}
      <Separator className="mb-1.5 flex-1 self-end bg-foreground/12" />
    </h3>
  );
}

const LABEL = "font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground";
const HEAD = `h-auto px-1.5 pb-1 ${LABEL}`;
const CELL = "px-1.5 py-2";
const ROW = "border-foreground/10";
const PANEL = "h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] data-ending-style:h-0 data-starting-style:h-0 motion-reduce:transition-none";

// A season as a table: week, opponent, the position's stat group, points, and the toggle that
// opens a row onto its breakdown. The table lines every stat up straight down the weeks.
function SeasonTable({ cols, first = "Wk", children }: { cols: string[]; first?: string; children: React.ReactNode }) {
  return (
    <Table className="tabular-nums">
      <TableHeader>
        <TableRow className={`${ROW} hover:bg-transparent`}>
          <TableHead className={`${HEAD} pl-0`}>{first}</TableHead>
          <TableHead className={HEAD}>Opp</TableHead>
          {cols.map((c) => <TableHead key={c} className={`${HEAD} text-right`}>{c}</TableHead>)}
          <TableHead className={`${HEAD} text-right`}>Pts</TableHead>
          <TableHead className={`${HEAD} w-0 pr-0`}><span className="sr-only">Breakdown</span></TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>{children}</TableBody>
    </Table>
  );
}

// The row's toggle. The whole row takes the click; the button is the keyboard and screen-reader
// handle on it (its click bubbles to the row), so it carries no handler of its own.
function Toggle({ open, label }: { open: boolean; label: string }) {
  return (
    <Button variant="ghost" size="icon-xs" aria-expanded={open} aria-label={label} className="text-foreground/45">
      <ChevronDown aria-hidden className={`stroke-[2.5] transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none ${open ? "rotate-180" : ""}`} />
    </Button>
  );
}

// The weeks played, then the weeks still ahead under their own rule: the games to play with the
// opponent's rank against his position, and the bye where it falls. A bye still to come belongs ahead, so the rule falls at the first game to play or the first
// week past the current one, whichever comes first.
function Weeks({ s, pos, week }: { s: SeasonLog; pos: string; week: number }) {
  const cols = columns(s);
  const span = cols.length + 4;
  const split = s.weeks.findIndex((w) => w.status === "upcoming" || w.week > week);
  const done = split < 0 ? s.weeks : s.weeks.slice(0, split);
  const ahead = split < 0 ? [] : s.weeks.slice(split);
  const of = ahead.find((w) => w.opp_rank)?.opp_rank?.of;
  return (
    <SeasonTable cols={cols}>
      {done.map((w, i) => <WeekRows key={w.week} w={w} span={span} i={i + 1} />)}
      {ahead.length > 0 && (
        <TableRow className={`${ROW} hover:bg-transparent log-in`} style={{ "--i": done.length + 1 } as React.CSSProperties}>
          <TableCell colSpan={span} className="px-0 pt-5 pb-1">
            <div className="flex items-baseline justify-between gap-3">
              <span className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em]">Ahead</span>
              <span className={LABEL}>{pos} pts/g allowed · rank{of ? `, ${ordinal(of)} = most` : ""}</span>
            </div>
          </TableCell>
        </TableRow>
      )}
      {ahead.map((w, i) => w.status === "upcoming"
        ? <AheadRow key={w.week} w={w} pos={pos} span={cols.length + 1} i={done.length + i + 2} />
        : <WeekRows key={w.week} w={w} span={span} i={done.length + i + 2} />)}
    </SeasonTable>
  );
}

// A game still to play: the week, the opponent, and across the stat and points columns how that
// defence has held his position, per game and as ESPN's rank. Nothing opens; there is no box yet.
function AheadRow({ w, pos, span, i }: { w: WeekLog; pos: string; span: number; i: number }) {
  return (
    <TableRow className={`${ROW} hover:bg-transparent log-in`} style={{ "--i": i } as React.CSSProperties}>
      <TableCell className={`${CELL} pl-0 font-display text-sm font-semibold text-muted-foreground`}>{weekLabel(w)}</TableCell>
      <TableCell className={CELL}><Opponent w={w} /></TableCell>
      <TableCell colSpan={span} className={`${CELL} text-right`}><OppRankCell r={w.opp_rank} pos={pos} /></TableCell>
      <TableCell className="py-2 pr-0 pl-1" />
    </TableRow>
  );
}

// Points allowed per game, a bar running the rank's length (a longer bar gave up more), and the
// rank. The bar is ink, not a verdict colour: it is context for the call, never the call.
function OppRankCell({ r, pos }: { r: OppRank | null | undefined; pos: string }) {
  if (!r) return <span className={LABEL}>no rank yet</span>;
  return (
    <span className="inline-flex items-center justify-end gap-2.5">
      <span aria-hidden className="text-xs text-foreground/70">{pts(r.allowed)}<span className="text-muted-foreground">/g</span></span>
      <span aria-hidden className="relative h-1 w-12 overflow-hidden rounded-full bg-foreground/10 sm:w-16">
        <span className="absolute inset-y-0 left-0 rounded-full bg-foreground/60" style={{ width: `${(r.rank / r.of) * 100}%` }} />
      </span>
      <span aria-hidden className="w-9 font-display text-base font-semibold leading-none">{ordinal(r.rank)}</span>
      <span className="sr-only">{ordinal(r.rank)} of {r.of} against {pos}s, {pts(r.allowed)} points allowed per game</span>
    </span>
  );
}

function Opponent({ w }: { w: WeekLog }) {
  if (!w.opp) return null;
  return (
    <span className="flex items-center gap-1 text-xs text-foreground/80">
      <span aria-hidden className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em]">{w.home ? "vs" : "@"}</span>
      <TeamLogo abbr={w.opp} size={16} fallback className="shrink-0" />
      <span aria-hidden className="hidden font-medium sm:inline">{w.opp}</span>
      <span className="sr-only">{w.home ? "vs" : "at"} {w.opp}</span>
    </span>
  );
}

// A week he played: the collapsed line, and beneath it a full-width row holding the week's
// breakdown, open or shut. A bye or a missed game is a quiet row that does not open.
// `i` places the row in the log's entrance (log-in), a beat after the one above it. A past
// season's weeks carry `fold` instead: always mounted, they fold open and shut with their season
// (globals.css, tr[data-fold]), and a shut one is inert so no tab stop or reader lands in it.
function WeekRows({ w, span, i, fold }: { w: WeekLog; span: number; i?: number; fold?: boolean }) {
  const [open, setOpen] = useState(false);
  const enter = i == null ? "" : "log-in";
  const order = i == null ? undefined : { "--i": i } as React.CSSProperties;
  const folds = fold != null;
  const row = folds ? { "data-fold": fold ? "open" : "shut", inert: !fold } : {};
  const label = (
    <TableCell className={`${CELL} pl-0 font-display text-sm font-semibold text-muted-foreground`}>
      <Fold on={folds}>
        {/* a game in progress: a lime dot before the week, not a chip in the narrow Opp column */}
        {w.status === "live" && <span aria-hidden className="mr-1 inline-block size-1.5 rounded-full bg-start align-middle motion-safe:animate-pulse" />}
        {weekLabel(w)}
      </Fold>
    </TableCell>
  );
  if (w.status === "bye" || w.status === "out") {
    return (
      <TableRow className={`${ROW} hover:bg-transparent ${enter}`} style={order} {...row}>
        {label}
        <TableCell colSpan={span - 1} className={`${CELL} ${LABEL}`}><Fold on={folds}>{w.status === "bye" ? "bye" : "did not play"}</Fold></TableCell>
      </TableRow>
    );
  }
  return (
    <>
      <TableRow className={`${ROW} cursor-pointer ${enter}`} style={order} onClick={() => setOpen((o) => !o)} {...row}>
        {label}
        <TableCell className={CELL}><Fold on={folds}><Opponent w={w} /></Fold></TableCell>
        {w.summary.map(([k, v]) => <TableCell key={k} className={`${CELL} text-right`}><Fold on={folds}>{v}</Fold></TableCell>)}
        <TableCell className={`${CELL} text-right font-display text-lg font-bold leading-none tracking-tight`}><Fold on={folds}>{ptsText(w.pts)}</Fold></TableCell>
        <TableCell className="py-2 pr-0 pl-1">
          <Fold on={folds}>
            <Toggle open={open} label={`Week ${w.week}${w.status === "live" ? " (live)" : ""}${w.opp ? `, ${w.home ? "vs" : "at"} ${w.opp}` : ""}: ${ptsText(w.pts)} points, breakdown`} />
          </Fold>
        </TableCell>
      </TableRow>
      <TableRow className="border-0 hover:bg-transparent" {...row}>
        <TableCell colSpan={span} className="p-0 whitespace-normal">
          <Fold on={folds} flush>
            <Collapsible open={open}>
              <CollapsibleContent className={PANEL}>
                <div className="my-2 rounded-md bg-foreground/[0.035] px-3 pt-2 pb-2.5">
                  {w.box.length > 0 ? <BoxTable box={w.box} /> : <p className="py-1 text-xs text-muted-foreground">Nothing that scores.</p>}
                </div>
              </CollapsibleContent>
            </Collapsible>
          </Fold>
        </TableCell>
      </TableRow>
    </>
  );
}

// A folding row's cell content: a one-track grid the fold runs from 1fr to 0fr, the clipped
// track, and inside it the cell's padding (`flush` for a cell that has none).
function Fold({ on, flush, children }: { on: boolean; flush?: boolean; children: React.ReactNode }) {
  return on ? <div className="fold" data-flush={flush || undefined}><div><div>{children}</div></div></div> : children;
}

// The past seasons in one table, newest first, so a season's totals read against the others.
function PastSeasons({ seasons }: { seasons: SeasonLog[] }) {
  const cols = columns(seasons[0]);
  return (
    <SeasonTable cols={cols} first="Yr">
      {seasons.map((s) => <PastSeason key={s.season} s={s} totals={s.totals!} span={cols.length + 4} />)}
    </SeasonTable>
  );
}

// A past season as one row: the year, games, the stat group summed, points with the per-game
// rate beneath. It opens onto his weeks, each of which opens onto its breakdown.
// The weeks are rendered on the first open, not with the log: three seasons of folded rows were
// enough work to stall the modal's grow on a phone as the log landed. That first open commits
// them shut (flushSync) and reads the layout, so the fold has a shut state to run from.
function PastSeason({ s, totals, span }: { s: SeasonLog; totals: SeasonTotals; span: number }) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const toggle = (e: React.MouseEvent<HTMLTableRowElement>) => {
    if (!mounted) {
      flushSync(() => setMounted(true));
      void e.currentTarget.offsetHeight;
    }
    setOpen((o) => !o);
  };
  return (
    <>
      <TableRow className={`${ROW} cursor-pointer`} onClick={toggle}>
        <TableCell className={`${CELL} pl-0 font-display text-sm font-semibold`}>{s.season}</TableCell>
        <TableCell className={`${CELL} text-xs text-foreground/80`}>{totals.games} G</TableCell>
        {totals.summary.map(([k, v]) => <TableCell key={k} className={`${CELL} text-right font-medium`}>{v}</TableCell>)}
        <TableCell className={`${CELL} text-right`}>
          <span className="block font-display text-lg font-bold leading-none tracking-tight">{pts(totals.pts)}</span>
          <span className="mt-0.5 block text-[0.6875rem] leading-none text-muted-foreground">{pts(totals.ppg)}/g</span>
        </TableCell>
        <TableCell className="py-2 pr-0 pl-1">
          <Toggle open={open} label={`${s.season} season: ${totals.games} games, ${pts(totals.pts)} points, weeks`} />
        </TableCell>
      </TableRow>
      {mounted && s.weeks.map((w) => <WeekRows key={w.week} w={w} span={span} fold={open} />)}
    </>
  );
}
