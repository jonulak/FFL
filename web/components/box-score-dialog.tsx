"use client";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { Player } from "@/lib/api";
import { boxSum } from "@/lib/box";
import { pts } from "@/lib/format";
import { BoxTable } from "./box-table";
import { BAND_CLOSE, PlayerHeader } from "./player-header";

// One player's points taken apart, in a matchup view: this week only, under the same team-colour
// header as the season modal. The items are summed here; when that sum and ESPN's posted number
// part (a stat correction, a feed a frame behind) both are shown.
export function BoxScoreDialog({ p, open, onOpenChange }: { p: Player | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={`gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-md ${BAND_CLOSE}`}>
        {p && <Body p={p} />}
      </DialogContent>
    </Dialog>
  );
}

function Body({ p }: { p: Player }) {
  const box = p.box ?? [];
  const off = box.length > 0 && Math.abs(boxSum(box) - p.actual) >= 0.05;
  return (
    <>
      <PlayerHeader p={p} />
      <div className="px-4 pt-3 pb-4">
        {box.length === 0 ? (
          <p className="py-3 text-sm text-muted-foreground">
            {p.bye ? "On bye this week." : p.game ? "Nothing that scores yet." : "No stats until his game kicks off."}
          </p>
        ) : (
          <BoxTable box={box} />
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
