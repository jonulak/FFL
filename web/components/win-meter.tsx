import { Meter } from "@base-ui/react/meter";
import { fill, outcome, pct } from "@/lib/win-prob";

// The odds as a tug of war from even: ink fills from the centre line toward the favourite's end by
// its margin over 50%, so a final runs the whole half to the winner and a coin flip shows nothing.
// No verdict colour (DESIGN.md, Three Verdicts Rule): the favourite reads off the fill's direction
// and length. Base UI's Meter rather than shadcn's Progress: this is a measurement, not a task
// advancing, and a screen reader should say so; its Indicator always grows from the left edge, so
// the fill is drawn here. `p` is the left side's odds; `teams` the left and right names.
export function WinMeter({ p, teams, size, final = false }: { p: number; teams: [string, string]; size: "bug" | "tile"; final?: boolean }) {
  const { left: from, width, end } = fill(p);
  const share = Math.max(p, 1 - p);
  const left = final ? outcome(p) : pct(p);
  const right = final ? outcome(1 - p) : pct(1 - p);
  const fav = end === "left" ? teams[0] : teams[1];
  const label = final
    ? (p === 0.5 ? `${teams[0]} and ${teams[1]} tied` : `${fav} won`)
    : `Win probability: ${fav} ${pct(share)}`;
  const track = (
    <Meter.Track className={`relative w-full overflow-hidden bg-foreground/12 ${size === "bug" ? "h-1.5 rounded-full" : "h-[3px]"}`}>
      <span aria-hidden style={{ left: `${from}%`, width: `${width}%` }}
        className="absolute inset-y-0 bg-foreground transition-[left,width] duration-[600ms] ease-[var(--ease-arrive)] motion-reduce:transition-none" />
      {/* even: the line the fill grows out of */}
      <span aria-hidden className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-foreground/45" />
    </Meter.Track>
  );
  if (size === "tile") {
    return (
      <Meter.Root value={Math.round(share * 100)} aria-label={label} className="block">
        {track}
      </Meter.Root>
    );
  }
  // From `sm` the numbers close the bar at each end; on the phone they sit above it so the bar
  // keeps the bug's whole width. The favourite's number is full ink, the other steps back. The
  // unit runs under it all, as every big numeral carries one.
  const num = "font-display text-lg font-bold leading-none tracking-tight tabular-nums sm:text-xl";
  const dim = "text-foreground/60";
  return (
    <Meter.Root value={Math.round(share * 100)} aria-label={label}
      className="grid grid-cols-2 items-center gap-x-3 gap-y-1.5 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
      <span aria-hidden className={`${num} ${end === "right" ? dim : ""}`}>{left}</span>
      <div className="col-span-2 row-start-2 sm:col-span-1 sm:col-start-2 sm:row-start-1">{track}</div>
      <span aria-hidden className={`${num} text-right sm:col-start-3 sm:row-start-1 ${end === "left" && p !== 0.5 ? dim : ""}`}>{right}</span>
      <span aria-hidden className="col-span-2 text-center font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground sm:col-span-3">
        {final ? "final" : "win prob"}
      </span>
    </Meter.Root>
  );
}
