"use client";

import type { OutlookBullet, Outlooked } from "@/lib/api";
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

// What a row's outlook needs: a Roster card, or a Team page row (which has no stale flag).
type OutlookOf = Outlooked & { name: string; pos: string; player_id: number; outlook_stale?: boolean };

// A row's outlook, written by the hourly run. It sits in the row's detail, which is already opened on a
// tap, so it is shown whole at every width rather than folded again on the phone.
export function Outlook({ c }: { c: OutlookOf }) {
  const { outlook: bullets, outlook_at: at, outlook_stale: stale, outlook_error: error } = c;
  const note = error
    ? <span key="error" className="fx-in inline-block text-out" title={error}>&nbsp;· last write failed</span>
    : null;

  if (!bullets?.length) {
    // the hourly run never writes a kicker or a defense
    const none = c.pos === "K" || c.pos === "D/ST" ? "No outlook for kickers and defenses." : "No outlook written yet.";
    return <p className="text-xs text-muted-foreground">{none}{note}</p>;
  }
  return (
    <div>
      {/* a new write that lands crossfades in over the bullets it replaces */}
      <Swap as="div" k={at ?? ""}><Bullets bullets={bullets} /></Swap>
      <p className="pt-1.5 text-xs text-muted-foreground">
        Outlook · {at ? <Ago iso={at} /> : "—"}
        <Presence show={!!stale}><span className="text-close-foreground dark:text-close"> · facts changed since</span></Presence>
        {note}
      </p>
    </div>
  );
}

// How old his outlook is, for the collapsed row: the short age under the disclosure chevron, so a glance down the
// list says which to recheck. Amber once the card's facts have changed since it was written (the stamp's "facts
// changed since"), red when the last write failed; nothing before the first.
export function outlookAge(c: OutlookOf, now: number): { age: string; tone: "fresh" | "stale" | "failed" } | null {
  if (!c.outlook?.length || !c.outlook_at) return null;
  return { age: shortAge(minutesSince(c.outlook_at, now)), tone: c.outlook_error ? "failed" : c.outlook_stale ? "stale" : "fresh" };
}

const AGE_TONE = { fresh: "text-muted-foreground", stale: "text-close-foreground dark:text-close", failed: "text-out" } as const;

// A new age (a minute on, a write landed) fades in.
export function OutlookAge({ c, now }: { c: OutlookOf; now: number }) {
  const a = outlookAge(c, now);
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
export function outlookAgeLabel(c: OutlookOf, now: number): string {
  const a = outlookAge(c, now);
  if (!a) return "";
  const when = a.age === "now" ? "just now" : `${a.age.slice(0, -1)} ${UNIT[a.age.slice(-1) as keyof typeof UNIT]} ago`;
  return `, outlook written ${when}${a.tone === "stale" ? ", facts changed since" : a.tone === "failed" ? ", last write failed" : ""}`;
}
