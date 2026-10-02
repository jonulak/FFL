"use client";

import { ChevronDown } from "lucide-react";
import { useState } from "react";
import type { OutlookBullet } from "@/lib/api";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Ago } from "./local-time";

// The bullets under a card. On a laptop it is simply there; on a phone the decision has to fit
// one screen, so it opens on a tap. The bullets are rendered twice on purpose: a plain block for
// md+ and the Collapsible for the phone. base-ui hides a closed panel with the `hidden`
// attribute, which the Tailwind preflight enforces with !important, so one panel cannot be
// "closed on the phone, open on a laptop" from CSS alone, and deciding it in JS would flash.
// The open/close is the page's one authored motion: base-ui's panel height under an
// exponential ease-out, no fade stacked on top.
// One row per label: the label in a fixed column so the text lines up, the sources trailing the
// sentence as small dotted links that open in a new tab.
function Bullets({ bullets }: { bullets: OutlookBullet[] }) {
  return (
    <dl className="max-w-[68ch] space-y-1 text-sm leading-relaxed text-muted-foreground">
      {bullets.map((b) => (
        <div key={b.label} className="flex gap-2">
          <dt className="w-14 shrink-0 pt-0.5 text-xs font-medium uppercase tracking-wide text-foreground/70">{b.label}</dt>
          <dd>
            {b.text}{" "}
            {b.sources.map((s, i) => (
              <span key={i} className="whitespace-nowrap text-xs">
                {i > 0 ? " · " : ""}
                <a href={s.url} target="_blank" rel="noreferrer"
                   className="underline decoration-dotted underline-offset-2 hover:text-foreground">
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

export function Outlook({ bullets, at, stale }: { bullets: OutlookBullet[] | null; at: string | null; stale: boolean }) {
  const [open, setOpen] = useState(false);
  if (!bullets?.length) {
    return <p className="text-xs text-muted-foreground">No outlook written yet.</p>;
  }
  const stamp = (
    <>
      Outlook · {at ? <Ago iso={at} /> : "—"}
      {stale ? <span className="text-close-foreground dark:text-close"> · facts changed since</span> : null}
    </>
  );
  return (
    <>
      <div className="hidden md:flex md:h-full md:flex-col">
        <Bullets bullets={bullets} />
        <p className="mt-auto pt-1.5 text-xs text-muted-foreground">{stamp}</p>
      </div>
      <Collapsible open={open} onOpenChange={setOpen} className="md:hidden">
        <CollapsibleTrigger className="group/outlook -my-1 flex min-h-8 w-full items-center gap-1.5 rounded-sm text-left text-xs text-muted-foreground">
          <ChevronDown className="size-3.5 shrink-0 transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] group-data-[panel-open]/outlook:rotate-180" />
          <span>{stamp}</span>
        </CollapsibleTrigger>
        <CollapsibleContent className="h-(--collapsible-panel-height) overflow-hidden transition-[height] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] data-[ending-style]:h-0 data-[starting-style]:h-0">
          <div className="pt-2"><Bullets bullets={bullets} /></div>
        </CollapsibleContent>
      </Collapsible>
    </>
  );
}
