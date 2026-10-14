"use client";

import { Spinner as Spin } from "@/components/ui/spinner";
import { cloneElement, isValidElement, useEffect, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode, type RefObject } from "react";

// The board's small motion vocabulary (globals.css, "State changes"): every state that changes
// under the reader says so, on the arrival curve, and none of it plays on a page's first paint.
//   Ticker   a number that runs from the old value to the new one; a points gain leaves a lime wash
//   Presence a chip or line that fades in when it appears and out when it goes
//   Swap     a word or label that crossfades when it changes
//   useReorder rows that slide to their new places when a list re-sorts or filters

const still = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
// the arrival curve, cubic-bezier(0.16, 1, 0.3, 1), near enough for a counter: a quartic ease-out
const arrive = (k: number) => 1 - Math.pow(1 - k, 4);

// The format for a whole-number Ticker (a count, an NFL score): the frames between two values round too.
export const whole = (n: number) => String(Math.round(n));

// A number that counts to its new value rather than swapping, so a score moving reads as movement.
// `gain` marks a fantasy-points figure: a rise there leaves the play feed's lime wash behind the
// numeral (a gain is the one change with a verdict colour); every other change only counts.
// Reduced motion lands on the value at once and keeps the wash.
export function Ticker({ value, format, gain = false, className = "" }: { value: number; format: (n: number) => string; gain?: boolean; className?: string }) {
  const [shown, setShown] = useState(value);
  const [wash, setWash] = useState(0);
  const at = useRef(value);
  const target = useRef(value);
  useEffect(() => {
    const from = at.current;
    const was = target.current;
    target.current = value;
    if (value === was) return;
    if (gain && value > was) setWash((w) => w + 1);
    if (still()) { at.current = value; setShown(value); return; }
    const t0 = performance.now();
    // a bigger jump takes a little longer, within a routine change's range
    const ms = Math.min(900, 420 + Math.abs(value - from) * 12);
    let raf = requestAnimationFrame(function step(t) {
      const k = Math.min(1, (t - t0) / ms);
      at.current = from + (value - from) * arrive(k);
      setShown(k === 1 ? value : at.current);
      if (k < 1) raf = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(raf);
  }, [value, gain]);
  return (
    <span className={`ticker ${className}`}>
      {wash > 0 && <span key={wash} aria-hidden className="ticker-wash" />}
      {format(shown)}
    </span>
  );
}

// Something that comes and goes with the data (the LIVE chip, a stale badge, an error line): it
// fades in when it appears and, held a beat after `show` turns false, fades out where it stood.
// The child must take a data attribute; `children` is still rendered while it leaves, so pass one
// that can draw without the data that turned it off. Nothing plays on the first paint.
// `fold` is for a block in a column (a card, a notice): its room opens as it fades in and closes
// after it, so what stands below slides rather than jumps. It wraps the child in two divs (kept
// whatever the state, so the child never remounts); the clip lasts only as long as the fold.
export function Presence({ show, fold = false, children }: { show: boolean; fold?: boolean; children: ReactElement<Record<string, unknown>> }) {
  const [state, setState] = useState<"still" | "in" | "out" | "gone">(show ? "still" : "gone");
  const [was, setWas] = useState(show);
  if (was !== show) {
    setWas(show);
    setState(show ? "in" : "out");
  }
  useEffect(() => {
    if (state !== "out") return;
    const t = setTimeout(() => setState("gone"), still() ? 0 : fold ? 300 : 160);
    return () => clearTimeout(t);
  }, [state, fold]);
  if (state === "gone" || !isValidElement(children)) return null;
  const fx = state === "still" ? {} : { "data-presence": state, "aria-hidden": state === "out" ? true : undefined };
  if (!fold) return cloneElement(children, fx);
  return (
    <div className="fx-fold" {...fx}
      onAnimationEnd={(e) => { if (e.target === e.currentTarget && state === "in") setState("still"); }}>
      <div>{children}</div>
    </div>
  );
}

// A label that crossfades when it changes ("proj" to "4 to play" to "final", PROJ to PTS): keyed
// on the text, or on `k` for anything else (a number switching from projection to score). Only a
// change replays it, never the first paint.
// `as="div"` around block content.
export function Swap({ children, k, className = "", as: Tag = "span" }: { children: ReactNode; k?: string; className?: string; as?: "span" | "div" }) {
  const id = k ?? String(children);
  const [first] = useState(id);
  return <Tag key={id} className={`${id === first ? "" : "fx-swap"} ${className}`}>{children}</Tag>;
}

// A spinner beside a label that opens its own room as it fades in and closes it after, so the
// label slides over rather than jumps. `gap` is the flex gap it sits in, given back while it is
// shut (a negative margin class, e.g. "-mr-1.5"), so a shut spinner takes no room at all.
export function Spinner({ on, gap = "-mr-1.5", className = "size-4" }: { on: boolean; gap?: string; className?: string }) {
  return (
    <span aria-hidden className={`grid transition-[grid-template-columns,opacity,margin] duration-200 ease-(--ease-arrive) motion-reduce:transition-[opacity] ${on ? "grid-cols-[1fr] opacity-100" : `grid-cols-[0fr] opacity-0 ${gap}`}`}>
      <span className="overflow-hidden"><Spin aria-hidden role="presentation" className={`${className} ${on ? "" : "animate-none"}`} /></span>
    </span>
  );
}

// A list that re-sorts or filters in place: rows (`data-key`, unique) slide from where they stood
// to their new places, rows new to the list fade in. `dep` is whatever orders the list: only its
// change plays, but every commit re-reads the places (a row opening above moves the rest), so a
// trip starts from where each row last stood. Read against the list's own top, so a scroll in
// between moves nothing.
export function useReorder<E extends HTMLElement>(dep: unknown): RefObject<E | null> {
  const root = useRef<E>(null);
  const last = useRef<Map<string, number> | null>(null);
  const seen = useRef(dep);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const changed = seen.current !== dep;
    seen.current = dep;
    // a re-sort mid-slide: the last read caught the rows where they were drawn; drop the old
    // slides so the new places are read clean, and the new trips start from there
    if (changed) for (const a of el.getAnimations({ subtree: true })) if (a.id === "reorder") a.cancel();
    const top = el.getBoundingClientRect().top;
    const now = new Map<string, number>();
    const rows = [...el.querySelectorAll<HTMLElement>("[data-key]")];
    for (const r of rows) now.set(r.dataset.key!, r.getBoundingClientRect().top - top);
    const before = last.current;
    last.current = now;
    if (!before || !changed || still()) return;
    for (const r of rows) {
      const was = before.get(r.dataset.key!);
      if (was === undefined) {
        r.animate([{ opacity: 0 }, { opacity: 1 }], { id: "reorder", duration: 220, delay: 80, easing: "ease-out", fill: "backwards" });
        continue;
      }
      const dy = was - now.get(r.dataset.key!)!;
      if (Math.abs(dy) < 0.5) continue;
      r.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], {
        id: "reorder",
        duration: Math.round(Math.min(520, Math.max(260, 240 + Math.abs(dy) * 0.25))),
        easing: "cubic-bezier(0.16, 1, 0.3, 1)",
      });
    }
  });
  return root;
}
