"use client";

import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { Player } from "@/lib/api";
import { boxSum, volumeText } from "@/lib/box";
import { pts } from "@/lib/format";
import { seasonRank } from "@/lib/player-log";
import { BoxTable } from "./box-table";
import { BAND_CLOSE, PlayerHeader } from "./player-header";
import { Heading, PlayerRecord, useLog, type LogFetch } from "./player-dialog";

// One player's points taken apart, in a matchup view: this week first, then his season log (this
// season's weeks and the games ahead, and the past seasons) as the season modal sets it, under the
// same team-colour header. The week's items are summed here; when that sum and ESPN's posted
// number part (a stat correction, a feed a frame behind) both are shown. The week follows the live
// feed while the dialog is open; the log is the one the tap asked for.
export function BoxScoreDialog({ p, week, log, open, onOpenChange }: {
  p: Player | null; week: number; log: LogFetch | null; open: boolean; onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={`grid max-h-[85dvh] grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-lg ${BAND_CLOSE}`}>
        {p && log && <Body key={p.player_id} p={p} week={week} log={log} />}
      </DialogContent>
    </Dialog>
  );
}

function Body({ p, week, log }: { p: Player; week: number; log: LogFetch }) {
  const { load, retry } = useLog(p.player_id, log);
  const box = p.box ?? [];
  const off = box.length > 0 && Math.abs(boxSum(box) - p.actual) >= 0.05;
  // only once his game is on: before kickoff an empty row still reads "0 targets"
  const vol = box.length > 0 || p.game ? volumeText(p.volume) : undefined;
  return (
    <>
      <PlayerHeader p={p} rank={load.state === "ready" ? seasonRank(load.log) : undefined}
        jersey={load.state === "ready" ? load.log.jersey : undefined} />
      <div className="overflow-y-auto overscroll-contain">
        <section aria-labelledby="box-week" className="space-y-1.5 px-4 pt-4">
          <Heading id="box-week" title={`Week ${week}`} note={box.length > 0 ? `${pts(p.actual)} pts` : undefined} />
          {box.length === 0 ? (
            <p className="py-1 text-sm text-muted-foreground">
              {p.bye ? "On bye this week." : p.game ? "Nothing that scores yet." : "No stats until his game kicks off."}
            </p>
          ) : (
            <BoxTable box={box} />
          )}
          <p className="pt-1.5 text-xs tabular-nums text-muted-foreground">
            {vol && <><span className="text-foreground/80">{vol}</span> <span aria-hidden>·</span> </>}
            proj {pts(p.proj)}
            {p.proj_sleeper != null && <> <span aria-hidden>·</span> ESPN {pts(p.proj_espn)}, Sleeper {pts(p.proj_sleeper)}</>}
            {off && <> <span aria-hidden>·</span> ESPN posts {pts(p.actual)}</>}
          </p>
        </section>
        <PlayerRecord load={load} retry={retry} fetch={log} className="px-4 pt-7 pb-5" />
      </div>
    </>
  );
}
