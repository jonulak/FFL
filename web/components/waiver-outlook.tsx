"use client";

import { NotebookText } from "lucide-react";
import { Spinner as Spin } from "@/components/ui/spinner";
import { useEffect, useRef, useState } from "react";
import { clientGet, clientPost } from "@/app/api-client";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import type { FreeAgentRow, PlayerLog, WaiverOutlook } from "@/lib/api";
import { seasonRank } from "@/lib/player-log";
import { outlookPhase } from "@/lib/waiver-outlook";
import { Ago } from "./local-time";
import { Bullets } from "./outlook";
import { hold, useGrow } from "./player-dialog";
import { BAND_CLOSE, PlayerHeader } from "./player-header";
import { Presence, Swap } from "./motion";

// A write is a minute or two of Opus with web search; reading the stored entry every few seconds
// is a local file read, so the dialog polls rather than holding a request open.
const POLL_MS = 5_000;

// A free agent's outlook, on demand: the Roster page's labelled, sourced bullets for a player I
// might claim. The trigger sits on the row's caption line; a filled dot marks one already written
// this week. Nothing is written until the button in the dialog is pressed, and closing the
// dialog never cancels a write — reopening picks it up.
// The tap reads the stored entry before opening, the way the season modal holds for its log: a
// dialog that opened on "Reading…" then snapped to the bullets' height around its centre read on
// the phone as opening low and jumping up. A slower read opens on "Reading…" and grows.
export function WaiverOutlookButton({ r }: { r: FreeAgentRow }) {
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState<FirstRead | null>(null);
  const [written, setWritten] = useState(r.has_outlook);
  const latest = useRef(0);
  const show = () => {
    const tap = ++latest.current;
    const read: FirstRead = {
      promise: clientGet<WaiverOutlook>(`/waivers/outlook/${r.player_id}`),
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
      <button type="button" onClick={show} aria-label={`${r.name}'s outlook${written ? "" : " (none written yet)"}`}
        className="inline-flex min-h-6 items-center gap-1 rounded-sm px-0.5 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-foreground/60 transition-colors hover:text-foreground focus-visible:outline-2">
        <NotebookText aria-hidden className="size-3.5 stroke-[2.25]" />
        outlook
        <Presence show={written}><span aria-hidden className="size-1.5 rounded-full bg-foreground/70" /></Presence>
      </button>
      {/* the dialog mounts with the first tap's read, not with the row: the Players table holds two
          of these buttons per free agent on 150 rows */}
      {first && (
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className={`gap-0 overflow-hidden rounded-lg p-0 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-xl ${BAND_CLOSE}`}>
            {open && <Body r={r} first={first} setWritten={setWritten} />}
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

// The tap's read, with `value` set as soon as it lands: the dialog opens on the bullets, not on
// "Reading…" for a render before the promise's callback runs.
type FirstRead = {
  promise: Promise<WaiverOutlook>; value?: WaiverOutlook;
  log: Promise<PlayerLog | null>; logValue?: PlayerLog | null;
};

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function Body({ r, first, setWritten }: { r: FreeAgentRow; first: FirstRead; setWritten: (w: boolean) => void }) {
  const [o, setO] = useState<WaiverOutlook | null>(first.value ?? null);
  const [log, setLog] = useState<PlayerLog | null>(first.logValue ?? null);
  useEffect(() => {
    let live = true;
    void first.log.then((l) => { if (live) setLog(l); });
    return () => { live = false; };
  }, [first]);
  const [fault, setFault] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const running = o?.running ?? false;

  // The tap's read first, then every POLL_MS while a write runs; re-armed when a write starts or
  // ends, stopped when the dialog closes (unmount).
  const opening = useRef<Promise<WaiverOutlook> | null>(first.promise);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const read = opening.current ?? clientGet<WaiverOutlook>(`/waivers/outlook/${r.player_id}`);
        opening.current = null;
        const next = await read;
        if (!live) return;
        setO(next);
        if (next.bullets?.length) setWritten(true);
        if (next.running) timer = setTimeout(tick, POLL_MS);
      } catch (e) {
        // a 404 is him leaving the wire since the page loaded
        if (live) setFault(message(e).startsWith("404") ? `${r.name} is no longer a free agent.` : message(e));
      }
    };
    void tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [r.player_id, r.name, running, setWritten]);

  async function write() {
    setStarting(true);
    setFault(null);
    try {
      await clientPost(`/waivers/outlook/${r.player_id}`);
    } catch (e) {
      // A 409 is either his write already running (a double tap, another tab), which is polled
      // like our own, or every slot busy with other players, which is said.
      const mine = message(e).startsWith("409")
        && (await clientGet<WaiverOutlook>(`/waivers/outlook/${r.player_id}`).catch(() => null))?.running;
      if (!mine) {
        setFault(message(e));
        return;
      }
    } finally {
      setStarting(false);
    }
    setO((prev) => ({ ...(prev ?? { bullets: null, at: null, stale: false, error: null }), running: true }));
  }

  const phase = outlookPhase(o);
  // The body and its footer follow the content from phase to phase (reading, writing, the
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
          {phase === "writing" && (
            <p className="flex items-start gap-2 py-3 text-sm text-muted-foreground">
              <Spin aria-hidden role="presentation" className="mt-0.5 size-4 shrink-0" />
              <span>Writing — usually a minute or two. You can close this; the write keeps going.</span>
            </p>
          )}
          {phase === "empty" && (
            <p className="py-3 text-sm text-muted-foreground">No outlook written for {r.name} this week.</p>
          )}
          {phase === "failed" && (
            <p className="py-3 text-sm text-out">The write failed: {o?.error}</p>
          )}
          {phase === "ready" && o?.bullets && <Bullets bullets={o.bullets} />}
          </Swap>
        </div>
        {/* pinned under the scrolling bullets, so the stamp and the button stay in reach on a phone */}
        <div className="border-t border-foreground/10 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <p className="text-xs text-muted-foreground">
              {phase === "ready" && o?.at && <>Outlook · <Ago iso={o.at} /></>}
              {phase === "ready" && o?.stale && <span className="text-close-foreground dark:text-close"> · facts changed since</span>}
              {phase === "ready" && o?.error && <span className="text-out"> · last rewrite failed</span>}
            </p>
            {(phase === "empty" || phase === "ready" || phase === "failed") && (
              <Button size="sm" variant={phase === "ready" ? "outline" : "default"} onClick={write} disabled={starting}>
                <Swap>{starting ? "Starting…" : phase === "ready" ? "Rewrite" : phase === "failed" ? "Try again" : "Write outlook"}</Swap>
              </Button>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
