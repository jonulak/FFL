"use client";

import { useRouter } from "@/components/league-link";
import { useOptimistic, useTransition } from "react";
import { Swap } from "./motion";
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue } from "@/components/ui/select";

export const PLAYOFFS_LABEL = "font-display text-[0.8125rem] font-semibold uppercase leading-none tracking-[0.12em] text-muted-foreground";

// The week is server state in `?week=`, so choosing one is a navigation the page re-renders
// from; the trigger takes the chosen week at once (crossfading to it) and dims while the page is
// on its way, then the page's own week takes over. `playoffs` are the fantasy playoff weeks
// (settings.json `playoff_weeks`): the list sets them apart under a Playoffs divider, ruled off
// from the weeks either side, and a playoff week chosen says so beside the trigger.
export function WeekSelect({ week, current, weeks, base, playoffs = [] }: { week: number; current: number; weeks: number; base: string; playoffs?: number[] }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [shown, setShown] = useOptimistic(week);
  const all = Array.from({ length: weeks }, (_, i) => i + 1);
  // no playoff weeks (a settings file without them): one run, every week
  const first = playoffs.length ? Math.min(...playoffs) : Infinity;
  const last = playoffs.length ? Math.max(...playoffs) : Infinity;
  const runs = [
    { key: "before", weeks: all.filter((n) => n < first), label: false },
    { key: "playoffs", weeks: all.filter((n) => playoffs.includes(n)), label: true },
    { key: "after", weeks: all.filter((n) => n > last), label: false },
  ].filter((r) => r.weeks.length > 0);
  const item = (n: number) => (
    <SelectItem key={n} value={String(n)}>
      <span className="tabular-nums">Week {n}</span>
      {n === current && <span className="text-xs text-muted-foreground">now</span>}
    </SelectItem>
  );
  return (
    <div className="flex items-center gap-2.5">
      {playoffs.includes(shown) && <span className={PLAYOFFS_LABEL}>Playoffs</span>}
      <Select
        value={String(shown)}
        onValueChange={(v) => {
          if (v == null || Number(v) === week) return;
          // scroll: false — the picker sits above the scoreboard, and the eye should stay there
          start(() => {
            setShown(Number(v));
            router.push(Number(v) === current ? base : `${base}?week=${v}`, { scroll: false });
          });
        }}
      >
        <SelectTrigger size="sm" aria-label="Week" data-pending={pending || undefined} className="pending-dim font-display text-[0.9375rem] font-semibold uppercase tracking-[0.12em]">
          <SelectValue><Swap>{`Week ${shown}`}</Swap></SelectValue>
        </SelectTrigger>
        <SelectContent align="end" alignItemWithTrigger={false}>
          {runs.map((r, i) => (
            <SelectGroup key={r.key}>
              {i > 0 && <SelectSeparator />}
              {r.label && <SelectLabel className={`${PLAYOFFS_LABEL} pt-2 pb-1.5`}>Playoffs</SelectLabel>}
              {r.weeks.map(item)}
            </SelectGroup>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
