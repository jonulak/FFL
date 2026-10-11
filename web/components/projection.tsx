"use client";

import type { ReactNode } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import type { Player } from "@/lib/api";
import { pts } from "@/lib/format";

// The week's projection is one number, the consensus of every source that has him (ffl/consensus.py),
// and `spread` says how far apart they were. Every board shows it as `±spread` beside the number,
// amber from 3 points (STRATEGY.md's close call); a tap on the number opens the sources behind it.

export const SOURCE_LABEL: Record<string, string> = {
  espn: "ESPN", sleeper: "Sleeper", cbs: "CBS", sharks: "FantasySharks", fleaflicker: "FleaFlicker",
};
// who projects which positions (K and D/ST: ESPN and Sleeper only), so the dialog can name the absent
const ALL = ["espn", "sleeper", "cbs", "sharks", "fleaflicker"];
const expected = (pos: string) => (pos === "K" || pos === "D/ST" ? ["espn", "sleeper"] : ALL);

export const SPLIT = 3;
const AMBER = "font-medium text-close-foreground dark:text-close";

// `±2.4`, or `1 source` when there was nothing to compare; nothing with no source at all.
export function Spread({ p, className = "" }: { p: Pick<Player, "spread" | "n_src">; className?: string }) {
  if (p.spread != null) {
    const wide = p.spread >= SPLIT;
    return (
      <span className={`tabular-nums ${wide ? AMBER : "text-muted-foreground"} ${className}`} title={`sources ${pts(p.spread)} apart${wide ? ": a close call" : ""}`}>
        <span aria-hidden>±{pts(p.spread)}</span>
        <span className="sr-only">, sources {pts(p.spread)} points apart</span>
      </span>
    );
  }
  return p.n_src === 1 ? <span className={`text-muted-foreground ${className}`}>1 source</span> : null;
}

// The number as a button that opens the sources. `children` is what stands on the page.
export function ProjectionTrigger({ p, week, className = "", label, children }: {
  p: Player; week?: number; className?: string; label?: string; children: ReactNode;
}) {
  return (
    <Dialog>
      <DialogTrigger
        render={<button type="button" />}
        aria-label={label ?? `${p.name}: projected ${pts(p.proj)}, show the sources`}
        className={`cursor-pointer rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${className}`}
      >
        {children}
      </DialogTrigger>
      <DialogContent className="gap-0 p-0 sm:max-w-md">
        <div className="flex items-start justify-between gap-4 px-5 pt-5 pr-12">
          <div className="min-w-0">
            <DialogTitle className="truncate font-display text-[1.375rem] font-semibold leading-tight tracking-tight">{p.name}</DialogTitle>
            <DialogDescription className="text-xs text-muted-foreground">
              {week != null ? `Week ${week} projection` : "This week's projection"} · {p.pos}
            </DialogDescription>
          </div>
        </div>
        <div className="px-5 pt-4 pb-5"><ProjectionSources p={p} /></div>
      </DialogContent>
    </Dialog>
  );
}

// The consensus and its spread, then every source on one shared scale: a dot per source, the
// band the spread measures (after dropping the highest and lowest once there are four), and the
// consensus as a line through all of them. Shared by the dialog and the Matchup box score.
export function ProjectionSources({ p }: { p: Player }) {
  const src = Object.entries(p.proj_src ?? {}).sort((a, b) => b[1] - a[1]);
  const missing = expected(p.pos).filter((s) => !(s in (p.proj_src ?? {})));
  const vals = src.map(([, v]) => v);
  const kept = vals.length >= 4 ? vals.slice(1, -1) : vals;
  const lo0 = Math.min(...vals, p.proj), hi0 = Math.max(...vals, p.proj);
  const pad = Math.max(1, (hi0 - lo0) * 0.12);
  const lo = Math.max(0, lo0 - pad), hi = hi0 + pad;
  const at = (v: number) => `${((v - lo) / (hi - lo || 1)) * 100}%`;
  const wide = p.spread != null && p.spread >= SPLIT;
  return (
    <div>
      <div className="flex items-baseline gap-2.5 tabular-nums">
        <span className="font-display text-[2.25rem] font-bold leading-none tracking-tight">{pts(p.proj)}</span>
        <span className="font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">proj</span>
        <Spread p={p} className="font-display text-[1.125rem] font-semibold" />
        <span className="ml-auto text-xs text-muted-foreground">{src.length} {src.length === 1 ? "source" : "sources"}</span>
      </div>
      {src.length === 0 ? (
        <p className="pt-3 text-sm text-muted-foreground">No source projects him this week.</p>
      ) : (
        <ul className="relative mt-4 grid grid-cols-[6.5rem_minmax(0,1fr)_3rem] items-center gap-x-3 gap-y-0 text-sm" aria-label="projection by source">
          {/* the consensus line and the spread band run behind every row's track */}
          <li aria-hidden className="pointer-events-none absolute inset-y-0 left-[7.25rem] right-[3.75rem]">
            {kept.length >= 2 && (
              <span className={`absolute inset-y-0 rounded-sm ${wide ? "bg-close/20" : "bg-foreground/[0.06]"}`}
                style={{ left: at(kept[kept.length - 1]), right: `calc(100% - ${at(kept[0])})` }} />
            )}
            <span className="absolute inset-y-0 w-px -translate-x-1/2 bg-foreground/70" style={{ left: at(p.proj) }} />
          </li>
          {src.map(([s, v]) => (
            <li key={s} className="contents">
              <span className="truncate py-2 font-medium">{SOURCE_LABEL[s] ?? s}</span>
              <span aria-hidden className="relative h-full">
                <span className="absolute top-1/2 left-0 right-0 h-px bg-foreground/10" />
                <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-foreground ring-2 ring-popover" style={{ left: at(v) }} />
              </span>
              <span className="py-2 text-right font-display text-base font-semibold tabular-nums">{pts(v)}</span>
            </li>
          ))}
        </ul>
      )}
      {missing.length > 0 && (
        <p className="mt-2 text-xs text-muted-foreground">Not projecting him: {missing.map((s) => SOURCE_LABEL[s]).join(", ")}</p>
      )}
      <p className="mt-3 border-t border-foreground/10 pt-3 text-xs leading-relaxed text-muted-foreground">
        The line is the consensus: the middle of the sources and their pairwise averages, so one source
        far off moves it little. The band is the ± range{vals.length >= 4 ? ", leaving out the highest and lowest" : ""}.
      </p>
    </div>
  );
}
