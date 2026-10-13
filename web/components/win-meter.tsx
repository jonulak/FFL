import { Meter } from "@base-ui/react/meter";
import { fill, halfFill, outcome, pct } from "@/lib/win-prob";
import { Swap, Ticker } from "./motion";

// What a screen reader hears: the favourite and its share, or the result once it is final.
// `p` is the first (left, or top) side's odds; `teams` the two names in that order.
function oddsLabel(p: number, teams: [string, string], final: boolean) {
  const fav = p >= 0.5 ? teams[0] : teams[1];
  if (final) return p === 0.5 ? `${teams[0]} and ${teams[1]} tied` : `${fav} won`;
  return `Win probability: ${fav} ${pct(Math.max(p, 1 - p))}`;
}

// The odds as a tug of war from even: ink fills from the centre line toward the favourite's end by
// its margin over 50%, so a final runs the whole half to the winner and a coin flip shows nothing.
// No verdict colour (DESIGN.md, Three Verdicts Rule): the favourite reads off the fill's direction
// and length. Base UI's Meter rather than shadcn's Progress: this is a measurement, not a task
// advancing, and a screen reader should say so; its Indicator always grows from the left edge, so
// the fill is drawn here. The Matchup scorebug's meter; a League card's is TileOdds + OddsHalf.
export function WinMeter({ p, teams, final = false }: { p: number; teams: [string, string]; final?: boolean }) {
  const { left: from, width, end } = fill(p);
  // the shares count with the fill; a final hands over to W/L in a crossfade
  const left = final ? outcome(p) : <Ticker value={p} format={pct} />;
  const right = final ? outcome(1 - p) : <Ticker value={1 - p} format={pct} />;
  // From `sm` the numbers close the bar at each end; on the phone they sit above it so the bar
  // keeps the bug's whole width. The favourite's number is full ink, the other steps back. The
  // unit runs under it all, as every big numeral carries one.
  const num = "font-display text-lg font-bold leading-none tracking-tight tabular-nums sm:text-xl";
  const dim = "text-foreground/60";
  return (
    <Meter.Root value={Math.round(Math.max(p, 1 - p) * 100)} aria-label={oddsLabel(p, teams, final)}
      className="grid grid-cols-2 items-center gap-x-3 gap-y-1.5 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
      <span aria-hidden className={`${num} transition-colors duration-500 ${end === "right" ? dim : ""}`}><Swap k={String(final)}>{left}</Swap></span>
      <div className="col-span-2 row-start-2 sm:col-span-1 sm:col-start-2 sm:row-start-1">
        <Meter.Track className="relative h-1.5 w-full overflow-hidden rounded-full bg-foreground/12">
          <span aria-hidden style={{ left: `${from}%`, width: `${width}%` }}
            className="absolute inset-y-0 bg-foreground transition-[left,width] duration-[600ms] ease-[var(--ease-arrive)] motion-reduce:transition-none" />
          {/* even: the line the fill grows out of */}
          <span aria-hidden className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-foreground/45" />
        </Meter.Track>
      </div>
      <span aria-hidden className={`${num} text-right transition-colors duration-500 sm:col-start-3 sm:row-start-1 ${end === "left" && p !== 0.5 ? dim : ""}`}><Swap k={String(final)}>{right}</Swap></span>
      <span aria-hidden className="col-span-2 text-center font-display text-[0.75rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground sm:col-span-3">
        <Swap>{final ? "final" : "win prob"}</Swap>
      </span>
    </Meter.Root>
  );
}

// A League card's meter: the same fill from even turned on its side, as a stripe down the card's
// left edge in two halves, one inside each row, so its centre is the divider between the rows
// however tall either runs. The away (top) half fills up from the divider, the home half down;
// only the favourite's fills, and a final fills the winner's whole row. No numbers. TileOdds is
// the meter a screen reader reads; the halves are drawing only.
export function TileOdds({ p, teams, final = false }: { p: number; teams: [string, string]; final?: boolean }) {
  return <Meter.Root value={Math.round(Math.max(p, 1 - p) * 100)} aria-label={oddsLabel(p, teams, final)} className="sr-only" />;
}

// One row's half, against the row (which must be `relative`) and pushed out through the card's
// padding (`--card-spacing`) to its left edge.
export function OddsHalf({ p, half }: { p: number; half: "top" | "bottom" }) {
  return (
    <span aria-hidden className="absolute inset-y-0 left-[calc(-1*var(--card-spacing))] w-1.5 overflow-hidden bg-foreground/12">
      <span style={{ height: `${halfFill(p, half) * 100}%` }}
        className={`absolute inset-x-0 bg-foreground transition-[height] duration-[600ms] ease-[var(--ease-arrive)] motion-reduce:transition-none ${half === "top" ? "bottom-0" : "top-0"}`} />
    </span>
  );
}
