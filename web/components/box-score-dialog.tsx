"use client";

import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import type { BoxItem, Player } from "@/lib/api";
import { pts } from "@/lib/format";
import { GameLine } from "./local-time";

// A count as the box score prints it: 250, 1.5 sacks.
const count = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));
// Points to the hundredth when a yardage rate leaves one (5.52), else to the tenth (8.0), so
// the line items add up on the page to the total beneath them.
const points = (n: number) => {
  const s = Math.abs(n).toFixed(2).replace(/0$/, "");
  return n < 0 ? `−${s}` : s;
};
// The league's rate: "+0.04 each", "−2 each"; a band row is a flat award, "+1 flat".
const rule = (b: BoxItem) => `${b.rate < 0 ? "−" : "+"}${Math.abs(b.rate)} ${b.flat ? "flat" : "each"}`;

const HEAD = "pb-1.5 font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground";

// One player's points taken apart: every stat the league scores, its count, the rate it is
// scored at and what it earned, ruled off to the total. The items are summed here; when that
// sum and ESPN's posted number part (a stat correction, a feed a frame behind) both are shown.
export function BoxScoreDialog({ p, open, onOpenChange }: { p: Player | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-0 rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-md">
        {p && <Body p={p} />}
      </DialogContent>
    </Dialog>
  );
}

function Body({ p }: { p: Player }) {
  const box = p.box ?? [];
  const sum = Math.round(box.reduce((t, b) => t + b.points, 0) * 100) / 100;
  const off = box.length > 0 && Math.abs(sum - p.actual) >= 0.05;
  return (
    <>
      <DialogHeader className="gap-1.5 border-b border-foreground/10 px-4 pt-4 pb-3 pr-12">
        <DialogTitle className="flex flex-wrap items-baseline gap-x-2 font-display text-[1.5rem] font-semibold leading-none tracking-tight">
          {p.name}
          <span className="font-sans text-xs font-semibold text-muted-foreground">{p.pos}</span>
          {p.injury !== "ACTIVE" && p.injury !== "NORMAL" && <Badge variant="muted" shape="chip">{p.injury.replaceAll("_", " ")}</Badge>}
        </DialogTitle>
        <DialogDescription render={<div />} className="text-xs text-muted-foreground">
          <GameLine p={p} size={16} />
        </DialogDescription>
      </DialogHeader>
      <div className="px-4 pt-3 pb-4">
        {box.length === 0 ? (
          <p className="py-3 text-sm text-muted-foreground">
            {p.bye ? "On bye this week." : p.game ? "Nothing that scores yet." : "No stats until his game kicks off."}
          </p>
        ) : (
          <table className="w-full text-sm tabular-nums">
            <thead>
              <tr className="border-b border-foreground/10">
                <th scope="col" className={`${HEAD} text-left`}>Stat</th>
                <th scope="col" className={`${HEAD} text-right`}>Count</th>
                <th scope="col" className={`${HEAD} pl-3 text-right`}>Scoring</th>
                <th scope="col" className={`${HEAD} pl-3 text-right`}>Pts</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-foreground/10">
              {box.map((b) => (
                <tr key={b.stat_id}>
                  <th scope="row" className="py-2 pr-2 text-left font-normal leading-snug">{b.label}</th>
                  <td className="py-2 text-right">{count(b.count)}</td>
                  <td className="py-2 pl-3 text-right whitespace-nowrap text-muted-foreground">{rule(b)}</td>
                  <td className={`py-2 pl-3 text-right font-medium ${b.points < 0 ? "text-out" : ""}`}>{points(b.points)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-foreground/35">
                <th scope="row" colSpan={3} className="pt-2.5 text-left font-display text-xs font-semibold uppercase tracking-[0.12em] text-muted-foreground">Total</th>
                <td className="pt-2 pl-3 text-right font-display text-[1.5rem] font-bold leading-none tracking-tight">{points(sum)}</td>
              </tr>
            </tfoot>
          </table>
        )}
        <p className="mt-3 text-xs tabular-nums text-muted-foreground">
          proj {pts(p.proj)}
          {p.proj_sleeper != null && <> <span aria-hidden>·</span> ESPN {pts(p.proj_espn)}, Sleeper {pts(p.proj_sleeper)}</>}
          {off && <> <span aria-hidden>·</span> ESPN posts {pts(p.actual)}</>}
        </p>
      </div>
    </>
  );
}
