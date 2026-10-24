import { ArrowDown, ArrowUp } from "lucide-react";
import type { Form } from "@/lib/api";
import { formItems, type Trend } from "@/lib/format";

// The opportunity trend as a drawn arrow beside the number, never a glyph: 12px lucide stroke,
// the latest game up or down against the earlier ones in the window.
export function TrendArrow({ trend, className = "" }: { trend: Trend | undefined; className?: string }) {
  if (!trend) return null;
  const Icon = trend === "up" ? ArrowUp : ArrowDown;
  return <Icon aria-label={trend} className={`inline size-3 stroke-[2.5] align-[-1px] ${className}`} />;
}

export type Fact = { key: string; label: string; value: string; title?: string; trend?: Trend };

// A row of facts as label/value pairs on one line (the league's strategy, "Recent form" (`uv run python -m ffl.strategy show`); the ROS figures in
// the player dialog): condensed small-caps label, the number in medium ink beside it. Each pair stays on
// one line; the line wraps between pairs. Nothing here is a verdict.
export function FactStrip({ items, className = "" }: { items: Fact[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <p className={`flex flex-wrap gap-x-2 gap-y-0.5 text-xs tabular-nums text-muted-foreground ${className}`}>
      {items.map((i, n) => (
        <span key={i.key} className="whitespace-nowrap" title={i.title}>
          {n > 0 && <span aria-hidden className="mr-2">·</span>}
          <span className="font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em]">{i.label}</span>
          {" "}
          <span className="font-medium text-foreground/85">{i.value}<TrendArrow trend={i.trend} className="ml-0.5" /></span>
        </span>
      ))}
    </p>
  );
}

// His last three games as one FactStrip: the Roster card's register for a row of facts, and
// the player dialog's Form block, the whole line the Players table shows only part of.
export function FormStrip({ form, className = "" }: { form: Form | null | undefined; className?: string }) {
  if (!form) return null;
  return <FactStrip items={formItems(form)} className={className} />;
}
