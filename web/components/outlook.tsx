"use client";

import { RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Spinner as Spin } from "@/components/ui/spinner";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clientGet, clientPost } from "@/app/api-client";
import type { Card, CardOutlook, OutlookBullet } from "@/lib/api";
import { minutesSince, shortAge } from "@/lib/format";
import { Ago } from "./local-time";
import { Presence, Swap } from "./motion";

// One block per label: the label as a small heading over its text, so the sentence gets the card's full
// width, and the sources trailing it as small dotted links that open in a new tab. Each link keeps its
// site and date together; the list itself wraps, or a long one runs past the card's clipped edge.
export function Bullets({ bullets }: { bullets: OutlookBullet[] }) {
  return (
    <dl className="max-w-[68ch] space-y-2 text-sm leading-relaxed text-muted-foreground">
      {bullets.map((b) => (
        <div key={b.label} className="min-w-0">
          <dt className="text-xs font-medium uppercase tracking-wide text-foreground/70">{b.label}</dt>
          <dd className="break-words">
            {b.text}{" "}
            {b.sources.map((s, i) => (
              <span key={i} className="text-xs">
                {i > 0 ? " · " : ""}
                <a href={s.url} target="_blank" rel="noreferrer"
                   className="whitespace-nowrap underline decoration-dotted underline-offset-2 transition-colors hover:text-foreground">
                  {s.site} {s.date}
                </a>
              </span>
            ))}
          </dd>
        </div>
      ))}
    </dl>
  );
}

// A rewrite is a minute or two of Opus with web search; its entry is a local file read, so the
// card polls that every few seconds rather than holding a request open.
const POLL_MS = 5_000;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// One card's rewrite: POST starts it, the GET is polled while it runs (from the click, or from a
// page load that found it running), and when it lands the server render is refreshed so the new
// bullets arrive through the page's own data. A 409 for his own write (a double tap, another tab)
// is followed like ours; any other refusal is said beside the button.
function useRewrite(c: Card) {
  const router = useRouter();
  const [mine, setMine] = useState(false);
  const [fault, setFault] = useState<string | null>(null);
  const running = mine || c.outlook_running;

  useEffect(() => {
    if (!running) return;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const o = await clientGet<CardOutlook>(`/outlook/${c.player_id}`);
        if (!live) return;
        if (o.outlook_running) {
          timer = setTimeout(tick, POLL_MS);
          return;
        }
        setMine(false);
        router.refresh();
      } catch (e) {
        if (!live) return;
        setMine(false);
        setFault(message(e).startsWith("404") ? `${c.name} is no longer on the roster.` : message(e));
      }
    };
    timer = setTimeout(tick, POLL_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [running, c.player_id, c.name, router]);

  async function rewrite() {
    setFault(null);
    setMine(true);
    try {
      await clientPost(`/outlook/${c.player_id}`);
    } catch (e) {
      const ours = message(e).startsWith("409")
        && (await clientGet<CardOutlook>(`/outlook/${c.player_id}`).catch(() => null))?.outlook_running;
      if (!ours) {
        setMine(false);
        setFault(message(e).replace(/^409 [^:]*:\s*/, ""));
      }
    }
  }
  return { running, fault, rewrite };
}

// Sits at the right end of the stamp line, in the waiver row's small-caps action voice. Disabled
// while the page-wide run writes every card: that run rewrites this one too.
function RewriteButton({ c, running, runBusy, onClick }: { c: Card; running: boolean; runBusy: boolean; onClick: () => void }) {
  const verb = c.outlook?.length ? "Rewrite" : "Write";
  return (
    <Button type="button" variant="ghost" size="xs" onClick={onClick} disabled={running || runBusy}
      aria-label={running ? `Writing ${c.name}'s outlook` : `${verb} ${c.name}'s outlook`}
      title={runBusy && !running ? "The full run is writing every card" : undefined}
      className="-my-1 -mr-1 h-auto min-h-8 rounded-sm px-1 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-foreground/60 hover:bg-transparent hover:text-foreground disabled:text-muted-foreground/70 disabled:opacity-100">
      <Swap k={running ? "running" : verb} className="inline-flex items-center gap-1">
        {running
          ? <Spin aria-hidden role="presentation" className="size-3.5 stroke-[2.25]" />
          : <RotateCw aria-hidden className="size-3.5 stroke-[2.25]" />}
        {running ? "Writing" : verb}
      </Swap>
    </Button>
  );
}

// A Roster row's outlook. It sits in the row's detail, which is already opened on a tap, so it is shown
// whole at every width rather than folded again on the phone.
export function Outlook({ c, runBusy }: { c: Card; runBusy: boolean }) {
  const { running, fault, rewrite } = useRewrite(c);
  const { outlook: bullets, outlook_at: at, outlook_stale: stale, outlook_error: error } = c;
  const button = <RewriteButton c={c} running={running} runBusy={runBusy} onClick={rewrite} />;
  // a refusal from this click wins over the stored failure of an earlier one
  const note = fault
    ? <span key="fault" className="fx-in inline-block text-out">&nbsp;· {fault}</span>
    : error && !running
      ? <span key="error" className="fx-in inline-block text-out" title={error}>&nbsp;· last rewrite failed</span>
      : null;

  if (!bullets?.length) {
    return (
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground" aria-live="polite">
        <p className="min-w-0"><Swap>{running ? "Writing his outlook — a minute or two." : "No outlook written yet."}</Swap>{note}</p>
        {button}
      </div>
    );
  }
  const stamp = (
    <>
      Outlook · {at ? <Ago iso={at} /> : "—"}
      <Presence show={stale && !running}><span className="text-close-foreground dark:text-close"> · facts changed since</span></Presence>
      {note}
    </>
  );
  // the old bullets stay readable while the new ones are written, set back a step
  const fade = `transition-opacity duration-300 ${running ? "opacity-55" : ""}`;
  return (
    <div aria-busy={running} aria-live="polite">
      {/* a rewrite that lands crossfades in over the bullets it replaces */}
      <Swap as="div" k={at ?? ""} className={fade}><Bullets bullets={bullets} /></Swap>
      <div className="flex items-center justify-between gap-3 pt-1.5">
        <p className="min-w-0 text-xs text-muted-foreground">{stamp}</p>
        {button}
      </div>
    </div>
  );
}

// How old his outlook is, for the collapsed row: the short age under the disclosure chevron, so a glance down the
// list says which to recheck. Amber once the card's facts have changed since it was written (the stamp's "facts
// changed since"), red when the last rewrite failed, a spinner while one is being written; nothing before the first.
export function outlookAge(c: Card, now: number): { age: string; tone: "fresh" | "stale" | "failed" } | null {
  if (!c.outlook?.length || !c.outlook_at) return null;
  return { age: shortAge(minutesSince(c.outlook_at, now)), tone: c.outlook_error ? "failed" : c.outlook_stale ? "stale" : "fresh" };
}

const AGE_TONE = { fresh: "text-muted-foreground", stale: "text-close-foreground dark:text-close", failed: "text-out" } as const;

// The spinner and the age crossfade into each other; a new age (a minute on, a rewrite landed) fades in.
export function OutlookAge({ c, now }: { c: Card; now: number }) {
  const a = outlookAge(c, now);
  if (c.outlook_running) {
    return <Swap k="running" className="flex"><Spin aria-hidden role="presentation" className="size-3 stroke-[2.5] text-muted-foreground" /></Swap>;
  }
  if (!a) return null;
  return (
    <Swap k={a.age} className="flex">
      <span aria-hidden suppressHydrationWarning
        className={`font-display text-[0.6875rem] font-semibold leading-none tracking-[0.04em] tabular-nums transition-colors duration-500 ${AGE_TONE[a.tone]}`}>
        {a.age}
      </span>
    </Swap>
  );
}

const UNIT = { m: "minutes", h: "hours", d: "days" } as const;

// The same, read out on the chevron's label.
export function outlookAgeLabel(c: Card, now: number): string {
  if (c.outlook_running) return ", outlook being written";
  const a = outlookAge(c, now);
  if (!a) return "";
  const when = a.age === "now" ? "just now" : `${a.age.slice(0, -1)} ${UNIT[a.age.slice(-1) as keyof typeof UNIT]} ago`;
  return `, outlook written ${when}${a.tone === "stale" ? ", facts changed since" : a.tone === "failed" ? ", last rewrite failed" : ""}`;
}
