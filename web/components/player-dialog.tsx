"use client";

import { ChevronDown } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { clientGet } from "@/app/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import type { PlayerLog, SeasonLog, SeasonTotals, WeekLog } from "@/lib/api";
import { pts } from "@/lib/format";
import { columns, ptsText, seasonPts, weekLabel } from "@/lib/player-log";
import { BoxTable } from "./box-table";
import { BAND_CLOSE, PlayerHeader, type PlayerSeed } from "./player-header";
import { TeamLogo } from "./player-media";
import { StaleBadge } from "./stale-badge";

// The season modal: tap a player anywhere outside a matchup view and his game log opens over the
// page. One dialog for the whole app, mounted in the layout; any row opens it through
// PlayerTrigger with what it already knows, so the header draws before the log arrives.

const Open = createContext<(p: PlayerSeed) => void>(() => {});

export function PlayerDialogProvider({ children }: { children: React.ReactNode }) {
  const [seed, setSeed] = useState<PlayerSeed | null>(null);
  const [open, setOpen] = useState(false);
  const show = useCallback((p: PlayerSeed) => { setSeed(p); setOpen(true); }, []);
  return (
    <Open.Provider value={show}>
      {children}
      <Dialog open={open && seed != null} onOpenChange={setOpen}>
        <DialogContent className={`grid max-h-[85dvh] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-lg ${BAND_CLOSE}`}>
          {seed && <Body key={seed.player_id} seed={seed} />}
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

function Body({ seed }: { seed: PlayerSeed }) {
  const [load, setLoad] = useState<Load>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    clientGet<PlayerLog>(`/player/${seed.player_id}`)
      .then((log) => current && setLoad({ state: "ready", log }))
      .catch(() => current && setLoad({ state: "error" }));
    return () => { current = false; };
  }, [seed.player_id, attempt]);
  const retry = () => { setLoad({ state: "loading" }); setAttempt((n) => n + 1); };
  return (
    <>
      <PlayerHeader p={seed} />
      <div className="overflow-y-auto overscroll-contain px-4 pt-4 pb-5">
        {load.state === "loading" && <Loading />}
        {load.state === "error" && (
          <div className="py-2">
            <p className="text-sm text-muted-foreground">ESPN&apos;s game log didn&apos;t load.</p>
            <Button size="sm" variant="outline" className="mt-2.5" onClick={retry}>Retry</Button>
          </div>
        )}
        {load.state === "ready" && <Seasons log={load.log} />}
      </div>
    </>
  );
}

function Loading() {
  return (
    <div aria-busy className="space-y-2" aria-label="loading the game log">
      <Skeleton className="h-5 w-24" />
      {Array.from({ length: 5 }, (_, i) => <Skeleton key={i} className="h-9 w-full" />)}
    </div>
  );
}

// This season's weeks, then last season folded into one totals row that opens onto its weeks.
function Seasons({ log }: { log: PlayerLog }) {
  const now = log.seasons.find((s) => s.season === log.season);
  const last = log.seasons.find((s) => s.season === log.season - 1);
  const played = seasonPts(now?.weeks ?? []);
  return (
    <div className="space-y-7">
      <section aria-labelledby="season-now" className="space-y-1.5">
        <Heading id="season-now" season={log.season} stale={log.stale_since}
          note={played.games > 0 ? `${played.games} ${played.games === 1 ? "game" : "games"} · ${pts(played.pts)} pts` : undefined} />
        {now && now.weeks.length > 0
          ? <Weeks s={now} />
          : <p className="py-1 text-sm text-muted-foreground">No games yet.</p>}
      </section>
      <section aria-labelledby="season-last" className="space-y-1.5">
        <Heading id="season-last" season={log.season - 1} />
        {last?.totals
          ? <LastSeason s={last} totals={last.totals} />
          : <p className="py-1 text-sm text-muted-foreground">No {log.season - 1} games.</p>}
      </section>
    </div>
  );
}

// A section heading as the Roster page sets its groups: the season in the board face, its count
// beside it, the hairline running out to the right.
function Heading({ id, season, note, stale }: { id: string; season: number; note?: string; stale?: string | null }) {
  return (
    <h3 id={id} className="flex items-baseline gap-3">
      <span className="font-display text-xl font-semibold uppercase tracking-[0.14em]">{season}</span>
      {note && <span className="font-display text-sm font-medium tabular-nums text-muted-foreground">{note}</span>}
      {stale && <StaleBadge since={stale} />}
      <Separator className="mb-1.5 flex-1 self-end bg-foreground/12" />
    </h3>
  );
}

// One grid for the column heads and every row of a season, so each stat reads straight down:
// week, opponent, the position's stat group, points, the chevron.
const grid = (n: number): React.CSSProperties => ({
  gridTemplateColumns: `2rem minmax(2.25rem,1fr) repeat(${n}, minmax(2rem,auto)) 3.25rem 0.875rem`,
});
const LABEL = "font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground";

function ColumnHeads({ cols }: { cols: string[] }) {
  return (
    <div aria-hidden className={`grid items-end gap-x-1.5 border-b border-foreground/10 pb-1 sm:gap-x-2.5 ${LABEL}`} style={grid(cols.length)}>
      <span>Wk</span>
      <span>Opp</span>
      {cols.map((c, i) => <span key={i} className="text-right">{c}</span>)}
      <span className="text-right">Pts</span>
      <span />
    </div>
  );
}

function Weeks({ s }: { s: SeasonLog }) {
  const cols = columns(s);
  return (
    <div>
      <ColumnHeads cols={cols} />
      {s.weeks.map((w) => <WeekRow key={w.week} w={w} n={cols.length} />)}
    </div>
  );
}

const ROW = "grid w-full items-center gap-x-1.5 py-2 text-left tabular-nums sm:gap-x-2.5";
const PANEL = "h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] data-ending-style:h-0 data-starting-style:h-0 motion-reduce:transition-none";

function Chevron() {
  return <ChevronDown aria-hidden className="size-3.5 stroke-[2.5] text-foreground/45 transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] group-data-panel-open:rotate-180 motion-reduce:transition-none" />;
}

function Opponent({ w }: { w: WeekLog }) {
  return (
    <span className="flex min-w-0 items-center gap-1 text-xs text-foreground/80">
      {w.opp && (
        <>
          <span aria-hidden className="font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em]">{w.home ? "vs" : "@"}</span>
          <TeamLogo abbr={w.opp} size={16} fallback className="shrink-0" />
          <span aria-hidden className="hidden font-medium sm:inline">{w.opp}</span>
          <span className="sr-only">{w.home ? "vs" : "at"} {w.opp}</span>
        </>
      )}
      {w.status === "live" && <Badge variant="muted" shape="chip" className="ml-0.5">live</Badge>}
    </span>
  );
}

// A week he played: the collapsed line, opening onto the week's full breakdown. A bye or a missed
// game is a quiet row that does not open.
function WeekRow({ w, n }: { w: WeekLog; n: number }) {
  if (w.status === "bye" || w.status === "out") {
    return (
      <div className={`${ROW} border-b border-foreground/10 text-muted-foreground`} style={grid(n)}>
        <span className="font-display text-sm font-semibold">{weekLabel(w)}</span>
        <span className={`${LABEL} col-[2/-1]`}>{w.status === "bye" ? "bye" : "did not play"}</span>
      </div>
    );
  }
  return (
    <Collapsible className="border-b border-foreground/10">
      <CollapsibleTrigger className={`group ${ROW} cursor-pointer rounded-sm outline-none hover:bg-foreground/[0.03] focus-visible:ring-2 focus-visible:ring-ring/60`} style={grid(n)}
        aria-label={`Week ${w.week}${w.opp ? `, ${w.home ? "vs" : "at"} ${w.opp}` : ""}: ${ptsText(w.pts)} points`}>
        <span className="font-display text-sm font-semibold text-muted-foreground">{weekLabel(w)}</span>
        <Opponent w={w} />
        {w.summary.map(([, v], i) => <span key={i} className="text-right text-sm">{v}</span>)}
        <span className="text-right font-display text-lg font-bold leading-none tracking-tight">{ptsText(w.pts)}</span>
        <Chevron />
      </CollapsibleTrigger>
      <CollapsibleContent className={PANEL}>
        <div className="mb-2.5 rounded-md bg-foreground/[0.035] px-3 pt-2 pb-2.5">
          {w.box.length > 0 ? <BoxTable box={w.box} /> : <p className="py-1 text-xs text-muted-foreground">Nothing that scores.</p>}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

// Last season as one row: games, the stat group summed, points with the per-game rate beneath.
// It opens onto his weeks, each of which opens onto its breakdown.
function LastSeason({ s, totals }: { s: SeasonLog; totals: SeasonTotals }) {
  const cols = columns(s);
  return (
    <div>
      <ColumnHeads cols={cols} />
      <Collapsible>
        <CollapsibleTrigger className={`group ${ROW} cursor-pointer rounded-sm outline-none hover:bg-foreground/[0.03] focus-visible:ring-2 focus-visible:ring-ring/60`} style={grid(cols.length)}
          aria-label={`${s.season} season: ${totals.games} games, ${pts(totals.pts)} points`}>
          <span className={LABEL}>All</span>
          <span className="text-xs text-foreground/80">{totals.games} G</span>
          {totals.summary.map(([, v], i) => <span key={i} className="text-right text-sm font-medium">{v}</span>)}
          <span className="flex flex-col items-end">
            <span className="font-display text-lg font-bold leading-none tracking-tight">{pts(totals.pts)}</span>
            <span className="mt-0.5 text-[0.6875rem] leading-none text-muted-foreground">{pts(totals.ppg)}/g</span>
          </span>
          <Chevron />
        </CollapsibleTrigger>
        <CollapsibleContent className={PANEL}>
          <div className="border-t border-foreground/10">
            {s.weeks.map((w) => <WeekRow key={w.week} w={w} n={cols.length} />)}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
