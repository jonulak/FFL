import type { PositionCap } from "@/lib/api";

// ESPN's per-position roster caps against what I hold (IR included): "QB 3/4 · TE 3/3", a full one
// in the foreground. Nothing when the API has no caps (settings.json from before position_limits).
export function PositionCaps({ caps, className = "" }: { caps?: PositionCap[]; className?: string }) {
  if (!caps?.length) return null;
  return (
    <p className={`flex flex-wrap gap-x-3 gap-y-0.5 text-xs tabular-nums text-muted-foreground ${className}`}>
      <span className="sr-only">Position caps:</span>
      {caps.map((c) => {
        const full = c.n >= c.limit;
        return (
          <span key={c.pos} className={full ? "font-semibold text-foreground" : undefined}
            title={full ? `${c.pos}: at ESPN's cap of ${c.limit}; an add needs a ${c.pos} drop` : `${c.pos}: ${c.n} of ESPN's cap of ${c.limit}`}>
            {c.pos} {c.n}/{c.limit}
          </span>
        );
      })}
    </p>
  );
}
