"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { clientGet } from "@/app/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { Decision, OppRank, PlayerLog, PlayerTx, SeasonLog, SeasonTotals, WeekLog } from "@/lib/api";
import { reasonText } from "@/lib/decisions";
import { ordinal, pts } from "@/lib/format";
import { cells, columns, hasSnaps, NAME_LINK, ptsText, seasonPts, seasonRank, weekLabel } from "@/lib/player-log";
import { volumeText } from "@/lib/box";
import { teamHref } from "@/lib/team-link";
import { clubLogoSrc } from "@/lib/teams";
import { HISTORY_LABEL, historyDetail, historyWhen } from "@/lib/transactions";
import { BoxTable } from "./box-table";
import { ClubLogo } from "./club-logo";
import { useDecisionPrefs } from "./decision-prefs";
import { BAND_CLOSE, PlayerHeader, type PlayerSeed } from "./player-header";
import { TeamLogo } from "./player-media";
import { SeasonFacts } from "./season-facts";
import { usePresets } from "./rationale-form";
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

// `history`: his moves (the draft pick included), asked for in the same tap so they land with the
// log (the API answers from cached payloads). A failure is no history: the section is left out.
// `decisions` are the journal rows that name him, from the same answer.
export type LogFetch = { promise: Promise<PlayerLog>; log?: PlayerLog; history: Promise<PlayerTx[]>; decisions: Promise<Decision[]> };
type Shown = { seed: PlayerSeed; fetch: LogFetch };

// A tap's request for the log and the history, with `log` set as soon as it lands.
export function fetchLog(id: number): LogFetch {
  const tx = clientGet<{ history: PlayerTx[]; decisions?: Decision[] }>(`/player/${id}/transactions`);
  const fetch: LogFetch = {
    promise: clientGet<PlayerLog>(`/player/${id}`),
    history: tx.then((d) => d.history, () => []),
    decisions: tx.then((d) => d.decisions ?? [], () => []),
  };
  // registered before any race on it, so `log` is set by the time the race settles
  fetch.promise.then((log) => { fetch.log = log; }, () => {});
  return fetch;
}

// Settles when the log lands or HOLD_MS passes, whichever is first: when to open a dialog that
// shows it (the Matchup breakdown waits on it the same way).
export function holdFor(fetch: LogFetch): Promise<void> {
  return hold(fetch.promise);
}

// The same wait over any first read a dialog opens on (the Players page's outlook).
export function hold(first: Promise<unknown>): Promise<void> {
  return Promise.race([first.then(() => {}, () => {}), new Promise<void>((r) => setTimeout(r, HOLD_MS))]);
}

export function PlayerDialogProvider({ children }: { children: React.ReactNode }) {
  const [shown, setShown] = useState<Shown | null>(null);
  const [open, setOpen] = useState(false);
  const latest = useRef(0);
  const show = useCallback((p: PlayerSeed) => {
    const tap = ++latest.current;
    const fetch = fetchLog(p.player_id);
    void holdFor(fetch).then(() => {
      if (tap !== latest.current) return; // a later tap took over
      setShown({ seed: p, fetch });
      setOpen(true);
    });
  }, []);
  return (
    <Open.Provider value={show}>
      {children}
      <Dialog open={open && shown != null} onOpenChange={setOpen}>
        <DialogContent className={`grid max-h-[calc(var(--vv-h,100dvh)*0.85)] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-lg ${BAND_CLOSE}`}>
          {shown && <Body key={shown.seed.player_id} seed={shown.seed} fetch={shown.fetch} />}
        </DialogContent>
      </Dialog>
    </Open.Provider>
  );
}

// The tap target: a row's tile and name, never the whole row, so the row's own controls (the
// compare chevron, the outlook button, Rewrite) keep their clicks. `pick` lets a matchup view open
// its own breakdown instead (the Matchup strip's plays); it answers false for a player it does not
// hold, and the season modal opens as anywhere else.
export function PlayerTrigger({ p, pick, className = "", children }: {
  p: PlayerSeed; pick?: (id: number) => boolean; className?: string; children: React.ReactNode;
}) {
  const show = useContext(Open);
  return (
    <button type="button" onClick={() => { if (!pick?.(p.player_id)) show(p); }}
      aria-label={`${p.name}: ${pick ? "scoring breakdown" : "game log"}`}
      className={`cursor-pointer text-left outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${className}`}>
      {children}
    </button>
  );
}

type Load = { state: "loading" } | { state: "ready"; log: PlayerLog } | { state: "error" };

// The log the tap already asked for; a retry asks again.
export function useLog(id: number, fetch: LogFetch) {
  const [load, setLoad] = useState<Load>(() => fetch.log ? { state: "ready", log: fetch.log } : { state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (attempt === 0 && fetch.log) return;
    let current = true;
    (attempt === 0 ? fetch.promise : clientGet<PlayerLog>(`/player/${id}`))
      .then((log) => current && setLoad({ state: "ready", log }))
      .catch(() => current && setLoad({ state: "error" }));
    return () => { current = false; };
  }, [id, fetch, attempt]);
  const retry = () => { setLoad({ state: "loading" }); setAttempt((n) => n + 1); };
  return { load, retry };
}

function Body({ seed, fetch }: { seed: PlayerSeed; fetch: LogFetch }) {
  const { load, retry } = useLog(seed.player_id, fetch);
  return (
    <>
      <PlayerHeader p={seed} week={load.state === "ready" ? load.log.current_week : undefined}
        rank={load.state === "ready" ? seasonRank(load.log) : undefined}
        jersey={load.state === "ready" ? load.log.jersey : undefined} />
      <div className="overflow-y-auto overscroll-contain">
        <FormSection p={seed} />
        <PlayerRecord load={load} retry={retry} fetch={fetch} className="px-4 pt-4 pb-5" />
      </div>
    </>
  );
}

// The Players row's Form section: the figures its table leaves to the dialog (SeasonFacts), when
// the row that opened it carried them. A row without ROS (the Matchup board's, a transaction's)
// shows nothing here.
function FormSection({ p }: { p: PlayerSeed }) {
  if (p.ros_adj == null) return null;
  return (
    <section aria-labelledby="season-form" className="log-in space-y-1 px-4 pt-4">
      <Heading id="season-form" title="Form" note={p.form ? `${p.form.weeks.length === 1 ? "week" : "weeks"} ${p.form.weeks.join(", ")}` : undefined} />
      <SeasonFacts p={{ ...p, ros_adj: p.ros_adj }} />
    </section>
  );
}

// His record, the data both player modals show under their own header (the season modal here,
// the Matchup breakdown in box-score-dialog.tsx, after its week): this season's weeks, the past
// seasons, then his transactions. One component so the two cannot drift: a section added here
// shows in both.
export function PlayerRecord({ load, retry, fetch, className = "" }: { load: Load; retry: () => void; fetch: LogFetch; className?: string }) {
  const [moves, setMoves] = useState<PlayerTx[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  useEffect(() => {
    let current = true;
    void fetch.history.then((h) => current && setMoves(h));
    void fetch.decisions.then((d) => current && setDecisions(d));
    return () => { current = false; };
  }, [fetch]);
  // under the seasons, once they are in: above the skeleton it would be pushed down by the log
  const history = load.state === "ready" && moves.length > 0;
  // Both inside the grow: the moves land with the log, and outside it their height arrived in one
  // frame, enough to snap the Matchup breakdown (its week already above) to the dialog's cap.
  // the section links into the Decisions page, so it goes when the page is turned off in Settings
  const logged = useDecisionPrefs().page && load.state === "ready" && decisions.length > 0;
  const grow = useGrow(`${load.state}${history ? "+moves" : ""}${logged ? "+decisions" : ""}`);
  return (
    <div ref={grow} className="overflow-hidden">
      <LogBody load={load} retry={retry} className={className} />
      {history && <History moves={moves} />}
      {logged && <Decisions rows={decisions} />}
    </div>
  );
}

// The journal rows that name him: what was decided and why, each linking to its week on the
// Decisions page (which closes the modal). Never decided on: no section.
function Decisions({ rows }: { rows: Decision[] }) {
  const presets = usePresets();
  return (
    <section aria-labelledby="season-decisions" className="log-in space-y-1.5 px-4 pt-2 pb-5">
      <Heading id="season-decisions" title="Decisions" note={String(rows.length)} />
      <ul className="divide-y divide-foreground/10">
        {rows.map((d) => {
          const reason = presets ? reasonText(d, presets) : null;
          const to = d.week != null ? `/decisions?week=${d.week}` : "/decisions";
          return (
            <li key={d.id} className="flex items-start gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <DialogClose nativeButton={false} render={<Link href={to} />} className={`block rounded-sm text-left font-medium leading-snug outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${NAME_LINK}`}>{d.title}</DialogClose>
                {d.rationale == null
                  ? <Badge variant="warn" className="mt-0.5">Needs a reason</Badge>
                  : reason && <p className="mt-0.5 text-xs leading-4 text-muted-foreground">{reason}</p>}
              </div>
              <time dateTime={d.at} className="shrink-0 text-xs tabular-nums text-muted-foreground">{d.at.slice(0, 10)}</time>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// His moves this season, newest first, his draft pick last: the fantasy team (its mark and name,
// which closes the modal onto that team's roster), what the move was in a muted chip, the date and
// week, and beneath it the other half of the pair, or where the pick fell. Undrafted and never
// moved: no section.
function History({ moves }: { moves: PlayerTx[] }) {
  return (
    <section aria-labelledby="season-moves" className="log-in space-y-1.5 px-4 pt-2 pb-5">
      <Heading id="season-moves" title="Transactions" note={String(moves.length)} />
      <ul className="divide-y divide-foreground/10">
        {moves.map((h) => {
          const href = teamHref(h.team_id);
          const detail = historyDetail(h);
          const name = <span className="truncate">{h.team.trim()}</span>;
          return (
            <li key={`${h.id}-${h.team_id}`} className="flex items-center gap-3 py-2.5">
              <ClubLogo src={clubLogoSrc(h)} abbrev={h.abbrev || h.team} size={32} className="shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="flex min-w-0 items-center font-display text-[1.0625rem] font-semibold leading-tight tracking-tight">
                  {href
                    ? <DialogClose nativeButton={false} render={<Link href={href} />} className={`min-w-0 truncate rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${NAME_LINK}`}>{name}</DialogClose>
                    : name}
                </p>
                {detail && <p className="mt-0.5 truncate text-xs leading-4 text-muted-foreground">{detail}</p>}
              </div>
              <div className="shrink-0 text-right">
                <Badge variant="muted" shape="chip">{HISTORY_LABEL[h.kind]}</Badge>
                <p className="mt-1 text-xs leading-4 tabular-nums text-muted-foreground">
                  {historyWhen(h)}
                </p>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// The log in its states: the skeleton, the error with its retry, the seasons.
function LogBody({ load, retry, className = "" }: { load: Load; retry: () => void; className?: string }) {
  return (
    <div className={className}>
      {load.state === "loading" && <Loading />}
      {load.state === "error" && (
        <div className="log-in py-2">
          <p className="text-sm text-muted-foreground">ESPN&apos;s game log didn&apos;t load.</p>
          <Button size="sm" variant="outline" className="mt-2.5" onClick={retry}>Retry</Button>
        </div>
      )}
      {load.state === "ready" && <Seasons log={load.log} />}
    </div>
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
// change no state and keep their own animation (a week's panel, a past season's fold). A change
// that lands mid-grow (the moves just after the log) carries on from the height on screen.
export function useGrow(state: string) {
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const running = el.getAnimations();
    const from = running.length ? el.offsetHeight : last.current;
    running.forEach((a) => a.cancel());
    const to = el.offsetHeight;
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
  // the season's snap share, over the games Sleeper has snaps for
  const snap = now?.totals?.snap_pct;
  return (
    <div className="space-y-7">
      <section aria-labelledby="season-now" className="log-in space-y-1.5">
        <Heading id="season-now" title={String(log.season)} stale={log.stale_since}
          note={played.games > 0 ? `${played.games} ${played.games === 1 ? "game" : "games"} · ${pts(played.pts)} pts${snap != null ? ` · ${snap}% snaps` : ""}` : undefined} />
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
export function Heading({ id, title, note, stale }: { id: string; title: string; note?: string; stale?: string | null }) {
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
const HEAD = `h-auto px-1 pb-1 sm:px-1.5 ${LABEL}`;
// a step tighter on a phone, where a QB's row (C/A, YDS, TD, INT, SNAP) has ten columns to fit
const CELL = "px-1 py-2 max-sm:text-[0.8125rem] sm:px-1.5";
const ROW = "border-foreground/10";
const PANEL = "h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] data-ending-style:h-0 data-starting-style:h-0 motion-reduce:transition-none";

// A season as a table: week, opponent (and this season, the opponent's rank), the position's stat
// group, points, and the toggle that opens a row onto its breakdown. The table lines every stat up
// straight down the weeks. The wrapper's overflow-x-auto makes its overflow-y auto too, and the rows
// arriving (log-in) start a few px low, so without overflow-y-hidden the entrance flashes a second
// scrollbar beside the table and the table narrows for it until the rows settle.
function SeasonTable({ cols, first = "Wk", ranked, children }: { cols: string[]; first?: string; ranked?: boolean; children: React.ReactNode }) {
  return (
    <Table className="tabular-nums" containerClassName="overflow-y-hidden">
      <TableHeader>
        <TableRow className={`${ROW} hover:bg-transparent`}>
          <TableHead className={`${HEAD} pl-0`}>{first}</TableHead>
          <TableHead className={HEAD}>Opp</TableHead>
          {ranked && <TableHead className={`${HEAD} text-right`}><abbr title="Opponent's rank against the position" className="no-underline">Rk</abbr></TableHead>}
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

// The weeks played, then the weeks still ahead under their own rule: each game to play with how
// that defence has held his position and his projection in the points column, and the bye where
// it falls. A bye still to come belongs ahead, so the rule falls at the first game to play or the
// first week past the current one, whichever comes first. Every game this season carries the
// opponent's rank. No legend under the table: he reads the columns without one.
function Weeks({ s, pos, week }: { s: SeasonLog; pos: string; week: number }) {
  const cols = columns(s);
  const snaps = hasSnaps(s);
  const span = cols.length + 5;
  const split = s.weeks.findIndex((w) => w.status === "upcoming" || w.week > week);
  const done = split < 0 ? s.weeks : s.weeks.slice(0, split);
  const ahead = split < 0 ? [] : s.weeks.slice(split);
  return (
    <>
      <SeasonTable cols={cols} ranked>
        {done.map((w, i) => <WeekRows key={w.week} w={w} span={span} i={i + 1} ranked pos={pos} snaps={snaps} />)}
        {ahead.length > 0 && (
          <TableRow className={`${ROW} hover:bg-transparent log-in`} style={{ "--i": done.length + 1 } as React.CSSProperties}>
            <TableCell colSpan={3} className="px-0 pt-5 pb-1 font-display text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.12em]">Ahead</TableCell>
            {cols.length > 0 && <TableCell colSpan={cols.length} className={`${HEAD} pt-5 text-right`}>{pos} pts/g allowed</TableCell>}
            <TableCell className={`${HEAD} pt-5 text-right`}>Proj</TableCell>
            <TableCell className="p-0" />
          </TableRow>
        )}
        {ahead.map((w, i) => w.status === "upcoming"
          ? <AheadRow key={w.week} w={w} pos={pos} cols={cols.length} i={done.length + i + 2} />
          : <WeekRows key={w.week} w={w} span={span} i={done.length + i + 2} snaps={snaps} />)}
      </SeasonTable>
    </>
  );
}

// A game still to play: the week, the opponent and its rank, how that defence has held his
// position per game, and his projection. Nothing opens; there is no box yet.
function AheadRow({ w, pos, cols, i }: { w: WeekLog; pos: string; cols: number; i: number }) {
  return (
    <TableRow className={`${ROW} hover:bg-transparent log-in`} style={{ "--i": i } as React.CSSProperties}>
      <TableCell className={`${CELL} pl-0 font-display text-sm font-semibold text-muted-foreground`}>{weekLabel(w)}</TableCell>
      <TableCell className={CELL}><Opponent w={w} /></TableCell>
      <TableCell className={`${CELL} text-right`}><Rank r={w.opp_rank} pos={pos} /></TableCell>
      {cols > 0 && <TableCell colSpan={cols} className={`${CELL} text-right`}><Allowed r={w.opp_rank} /></TableCell>}
      <TableCell className={`${CELL} text-right font-display text-lg font-semibold leading-none tracking-tight ${w.proj == null ? "text-muted-foreground" : "text-foreground/60"}`}>
        <span aria-hidden>{ptsText(w.proj ?? null)}</span>
        <span className="sr-only">{w.proj == null ? "no projection" : `projected ${pts(w.proj)} points`}</span>
      </TableCell>
      <TableCell className="py-2 pr-0 pl-1" />
    </TableRow>
  );
}

// The opponent's rank against his position, a bare number under the Rk heading (the caption reads
// it). A dash where ESPN has none for that club.
function Rank({ r, pos }: { r: OppRank | null | undefined; pos: string }) {
  if (!r) return <span className="text-muted-foreground"><span aria-hidden>—</span><span className="sr-only">no rank</span></span>;
  return (
    <span className="font-display text-sm font-semibold text-foreground/80">
      <span aria-hidden>{r.rank}</span>
      <span className="sr-only">{ordinal(r.rank)} of {r.of} against {pos}s</span>
    </span>
  );
}

// Points the position has scored per game against that defence, and a bar running the rank's
// length (a longer bar gave up more). The bar is ink, not a verdict colour: it is context for the
// call, never the call.
function Allowed({ r }: { r: OppRank | null | undefined }) {
  if (!r) return null;
  return (
    <span className="inline-flex items-center justify-end gap-2.5">
      <span className="text-xs text-foreground/70">{pts(r.allowed)}<span className="text-muted-foreground">/g<span className="sr-only"> allowed</span></span></span>
      <span aria-hidden className="relative h-1 w-12 overflow-hidden rounded-full bg-foreground/10 sm:w-16">
        <span className="absolute inset-y-0 left-0 rounded-full bg-foreground/60" style={{ width: `${(r.rank / r.of) * 100}%` }} />
      </span>
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
function WeekRows({ w, span, i, fold, ranked, pos = "", snaps = false }: { w: WeekLog; span: number; i?: number; fold?: boolean; ranked?: boolean; pos?: string; snaps?: boolean }) {
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
        {ranked && <TableCell className={`${CELL} text-right`}><Fold on={folds}><Rank r={w.opp_rank} pos={pos} /></Fold></TableCell>}
        {cells(w, snaps).map(([k, v]) => <TableCell key={k} className={`${CELL} text-right`}><Fold on={folds}>{v}</Fold></TableCell>)}
        <TableCell className={`${CELL} text-right`}>
          <Fold on={folds}>
            <span className="block font-display text-lg font-bold leading-none tracking-tight">{ptsText(w.pts)}</span>
            {w.pos_rank != null && (
              <span className="mt-0.5 block text-[0.6875rem] leading-none text-muted-foreground">
                <span aria-hidden>{pos}{w.pos_rank}</span>
                <span className="sr-only">{ordinal(w.pos_rank)} among {pos}s that week</span>
              </span>
            )}
          </Fold>
        </TableCell>
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
                  {w.volume && <p className="pt-1.5 text-xs tabular-nums text-muted-foreground">{volumeText(w.volume)}</p>}
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
