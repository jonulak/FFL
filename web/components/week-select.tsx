"use client";

import { useRouter } from "next/navigation";
import { useOptimistic, useTransition } from "react";
import { Swap } from "./motion";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// The week is server state in `?week=`, so choosing one is a navigation the page re-renders
// from; the trigger takes the chosen week at once (crossfading to it) and dims while the page is
// on its way, then the page's own week takes over.
export function WeekSelect({ week, current, weeks, base }: { week: number; current: number; weeks: number; base: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [shown, setShown] = useOptimistic(week);
  const all = Array.from({ length: weeks }, (_, i) => i + 1);
  return (
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
        {all.map((n) => (
          <SelectItem key={n} value={String(n)}>
            <span className="tabular-nums">Week {n}</span>
            {n === current && <span className="text-xs text-muted-foreground">now</span>}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
