"use client";

import { ChevronDown, LoaderCircle, RotateCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { clientGet, clientPost } from "@/app/api-client";
import type { Card, CardOutlook, OutlookBullet } from "@/lib/api";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Ago } from "./local-time";

// The bullets under a card. On a laptop it is simply there; on a phone the decision has to fit
// one screen, so it opens on a tap. The bullets are rendered twice on purpose: a plain block for
// md+ and the Collapsible for the phone. base-ui hides a closed panel with the `hidden`
// attribute, which the Tailwind preflight enforces with !important, so one panel cannot be
// "closed on the phone, open on a laptop" from CSS alone, and deciding it in JS would flash.
// The open/close is the page's one authored motion: base-ui's panel height under an
// exponential ease-out, no fade stacked on top.
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
                   className="whitespace-nowrap underline decoration-dotted underline-offset-2 hover:text-foreground">
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
    <button type="button" onClick={onClick} disabled={running || runBusy}
      aria-label={running ? `Writing ${c.name}'s outlook` : `${verb} ${c.name}'s outlook`}
      title={runBusy && !running ? "The full run is writing every card" : undefined}
      className="-my-1 -mr-1 inline-flex min-h-8 shrink-0 items-center gap-1 rounded-sm px-1 font-display text-[0.6875rem] font-semibold uppercase tracking-[0.12em] text-foreground/60 transition-colors hover:text-foreground focus-visible:outline-2 disabled:pointer-events-none disabled:text-muted-foreground/70">
      {running
        ? <LoaderCircle aria-hidden className="size-3.5 animate-spin stroke-[2.25] motion-reduce:animate-none" />
        : <RotateCw aria-hidden className="size-3.5 stroke-[2.25]" />}
      {running ? "Writing" : verb}
    </button>
  );
}

export function Outlook({ c, runBusy }: { c: Card; runBusy: boolean }) {
  const [open, setOpen] = useState(false);
  const { running, fault, rewrite } = useRewrite(c);
  const { outlook: bullets, outlook_at: at, outlook_stale: stale, outlook_error: error } = c;
  const button = <RewriteButton c={c} running={running} runBusy={runBusy} onClick={rewrite} />;
  // a refusal from this click wins over the stored failure of an earlier one
  const note = fault
    ? <span className="text-out"> · {fault}</span>
    : error && !running
      ? <span className="text-out" title={error}> · last rewrite failed</span>
      : null;

  if (!bullets?.length) {
    return (
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground" aria-live="polite">
        <p className="min-w-0">{running ? "Writing his outlook — a minute or two." : "No outlook written yet."}{note}</p>
        {button}
      </div>
    );
  }
  const stamp = (
    <>
      Outlook · {at ? <Ago iso={at} /> : "—"}
      {stale && !running ? <span className="text-close-foreground dark:text-close"> · facts changed since</span> : null}
      {note}
    </>
  );
  // the old bullets stay readable while the new ones are written, set back a step
  const fade = `transition-opacity duration-300 ${running ? "opacity-55" : ""}`;
  return (
    <div aria-busy={running} aria-live="polite" className="md:h-full">
      <div className="hidden md:flex md:h-full md:flex-col">
        <div className={fade}><Bullets bullets={bullets} /></div>
        <div className="mt-auto flex items-center justify-between gap-3 pt-1.5">
          <p className="min-w-0 text-xs text-muted-foreground">{stamp}</p>
          {button}
        </div>
      </div>
      <Collapsible open={open} onOpenChange={setOpen} className="md:hidden">
        <div className="flex items-center gap-2">
          <CollapsibleTrigger className="group/outlook -my-1 flex min-h-8 min-w-0 flex-1 items-center gap-1.5 rounded-sm text-left text-xs text-muted-foreground">
            <ChevronDown className="size-3.5 shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] group-data-[panel-open]/outlook:rotate-180" />
            <span>{stamp}</span>
          </CollapsibleTrigger>
          {button}
        </div>
        <CollapsibleContent className="h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] data-[ending-style]:h-0 data-[starting-style]:h-0">
          <div className={`pt-2 ${fade}`}><Bullets bullets={bullets} /></div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
