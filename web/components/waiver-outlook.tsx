"use client";

import { NotebookText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { clientGet } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { PlayerLog, PlayerOutlook, PlayerRow } from "@/lib/api";
import { seasonRank } from "@/lib/player-log";
import { outlookPhase } from "@/lib/waiver-outlook";
import { Ago } from "./local-time";
import { Bullets } from "./outlook";
import { hold, useGrow } from "./player-dialog";
import { BAND_CLOSE, PlayerHeader } from "./player-header";
import { Presence, Swap } from "./motion";

// A player's outlook, read on a tap: the Roster page's labelled, sourced bullets, written by the hourly
// run (never from here). The trigger sits on the row's caption line; a filled dot marks one written
// this week.
// The tap reads the stored entry before opening, the way the season modal holds for its log: a
// dialog that opened on "Reading…" then snapped to the bullets' height around its centre read on
// the phone as opening low and jumping up. A slower read opens on "Reading…" and grows.
export function WaiverOutlookButton({ r, week }: { r: PlayerRow; week: number }) {
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState<FirstRead | null>(null);
  const latest = useRef(0);
  const show = () => {
    const tap = ++latest.current;
    const read: FirstRead = {
      promise: clientGet<PlayerOutlook>(`/outlook/${r.player_id}?week=${week}`),
      // the header's jersey and season rank, as the season modal reads them; a failure leaves them off
      log: clientGet<PlayerLog>(`/player/${r.player_id}`).catch(() => null),
    };
    // registered before the hold's race, so the values are set by the time it settles; a failure is the Body's to report
    read.promise.then((v) => { read.value = v; }, () => {});
    void read.log.then((l) => { read.logValue = l; });
    void hold(Promise.all([read.promise, read.log])).then(() => {
      if (tap !== latest.current) return;
      setFirst(read);
      setOpen(true);
    });
  };
  return (
    <>
      <Button type="button" variant="ghost" size="xs" onClick={show} aria-label={`${r.name}'s outlook${r.has_outlook ? "" : " (none written yet)"}`}
        className="h-auto min-h-6 rounded-sm px-0.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-foreground/60 hover:bg-transparent dark:hover:bg-transparent hover:text-foreground">
        <NotebookText aria-hidden className="size-3.5 stroke-[2.25]" />
        outlook
        <Presence show={r.has_outlook}><span aria-hidden className="size-1.5 rounded-full bg-foreground/70" /></Presence>
      </Button>
      {/* the dialog mounts with the first tap's read, not with the row: the Players table holds two
          of these buttons per free agent on 150 rows */}
      {first && (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className={`gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-xl ${BAND_CLOSE}`}>
            {open && <Body r={r} first={first} />}
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

// The tap's read, with `value` set as soon as it lands: the dialog opens on the bullets, not on
// "Reading…" for a render before the promise's callback runs.
type FirstRead = {
  promise: Promise<PlayerOutlook>; value?: PlayerOutlook;
  log: Promise<PlayerLog | null>; logValue?: PlayerLog | null;
};

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Body({ r, first }: { r: PlayerRow; first: FirstRead }) {
  const [o, setO] = useState<PlayerOutlook | null>(first.value ?? null);
  const [log, setLog] = useState<PlayerLog | null>(first.logValue ?? null);
  const [fault, setFault] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void first.log.then((l) => { if (live) setLog(l); });
    first.promise.then((v) => { if (live) setO(v); }, (e) => { if (live) setFault(message(e)); });
    return () => { live = false; };
  }, [first]);

  const phase = outlookPhase(o);
  // The body and its footer follow the content from phase to phase (reading, then the
  // bullets), growing from the height on screen rather than snapping around the centre.
  const grow = useGrow(`${phase}${fault ? "+fault" : ""}`);
  return (
    <>
      {/* the season modal's band and game line, so the two dialogs read as one player's */}
      <PlayerHeader p={r} week={log?.current_week} rank={log ? seasonRank(log) : undefined} jersey={log?.jersey} />
      <div ref={grow} className="overflow-hidden">
        <div className="max-h-[60dvh] overflow-y-auto px-4 pt-3 pb-4" aria-live="polite">
          {/* each phase fades in as the dialog grows to it */}
          {fault && <p key={fault} className="log-in mb-3 text-sm text-out">{fault}</p>}
          <Swap as="div" k={phase}>
          {phase === "loading" && !fault && <p className="py-3 text-sm text-muted-foreground">Reading…</p>}
          {phase === "empty" && (
            <p className="py-3 text-sm text-muted-foreground">No outlook written for {r.name} this week.</p>
          )}
          {phase === "failed" && (
            <p className="py-3 text-sm text-out">The last write failed: {o?.error}</p>
          )}
          {phase === "ready" && o?.bullets && <Bullets bullets={o.bullets} />}
          </Swap>
        </div>
        {/* pinned under the scrolling bullets, so the stamp stays in sight on a phone */}
        {phase === "ready" && o?.at && (
          <p className="border-t border-foreground/10 px-4 py-3 text-xs text-muted-foreground">
            Outlook · <Ago iso={o.at} />
            {o.error && <span className="text-out"> · last write failed</span>}
          </p>
        )}
      </div>
    </>
  );
}
