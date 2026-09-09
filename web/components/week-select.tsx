"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

// The week is server state in `?week=`, so choosing one is a navigation the page re-renders
// from; the trigger dims while the new week is on its way.
export function WeekSelect({ week, current, weeks, base }: { week: number; current: number; weeks: number; base: string }) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const all = Array.from({ length: weeks }, (_, i) => i + 1);
  return (
    <Select
      value={String(week)}
      onValueChange={(v) => {
        if (v == null || Number(v) === week) return;
        start(() => router.push(Number(v) === current ? base : `${base}?week=${v}`));
      }}
    >
      <SelectTrigger size="sm" aria-label="Week" className={`font-display text-[15px] font-semibold uppercase tracking-[0.12em] ${pending ? "opacity-60" : ""}`}>
        <SelectValue>{`Week ${week}`}</SelectValue>
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
