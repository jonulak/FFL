import { Meter } from "@base-ui/react/meter";
import { outcome, pct } from "@/lib/win-prob";

// The odds as a tug of war: the left side's share in ink from the left edge, the right side's
// the faint remainder, a tick at even. No verdict colour (DESIGN.md, Three Verdicts Rule): the
// favourite reads off the bar's length. Base UI's Meter rather than shadcn's Progress: this is a
// measurement, not a task advancing, and a screen reader should say so. `p` is the left side's.
export function WinMeter({ p, team, size, final = false }: { p: number; team: string; size: "bug" | "tile"; final?: boolean }) {
  const left = final ? outcome(p) : pct(p);
  const right = final ? outcome(1 - p) : pct(1 - p);
  const label = final ? `${team}: ${left === "W" ? "won" : left === "L" ? "lost" : "tied"}` : `Win probability: ${team} ${left}`;
  const track = (
    <Meter.Track className={`relative w-full overflow-hidden bg-foreground/12 ${size === "bug" ? "h-1.5 rounded-full" : "h-[3px]"}`}>
      <Meter.Indicator className="h-full bg-foreground transition-[width] duration-[600ms] ease-[var(--ease-arrive)] motion-reduce:transition-none" />
      {/* even, as a notch cut in the bar: it reads over the ink fill and the faint remainder alike */}
      <span aria-hidden className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-card" />
    </Meter.Track>
  );
  if (size === "tile") {
    return (
      <Meter.Root value={Math.round(p * 100)} aria-label={label} className="block">
        {track}
      </Meter.Root>
    );
  }
  // From `sm` the numbers close the bar at each end; on the phone they sit above it so the bar
  // keeps the bug's whole width. The unit runs under it all, as every big numeral carries one.
  const num = "font-display text-lg font-bold leading-none tracking-tight tabular-nums sm:text-xl";
  return (
    <Meter.Root value={Math.round(p * 100)} aria-label={label}
      className="grid grid-cols-2 items-center gap-x-3 gap-y-1.5 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
      <span aria-hidden className={num}>{left}</span>
      <div className="col-span-2 row-start-2 sm:col-span-1 sm:col-start-2 sm:row-start-1">{track}</div>
      <span aria-hidden className={`${num} text-right text-foreground/60 sm:col-start-3 sm:row-start-1`}>{right}</span>
      <span aria-hidden className="col-span-2 text-center font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground sm:col-span-3">
        {final ? "final" : "win prob"}
      </span>
    </Meter.Root>
  );
}
