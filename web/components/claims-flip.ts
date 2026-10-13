"use client";

import { useLayoutEffect, useRef, type RefObject } from "react";

// The claim groups' changes of size and place, played out (FLIP). Whatever moved a card or a row
// (the cards folding for a group's drag and opening after it, a row crossing into another group, a
// row removed, a card's errors arriving with a save) lays out at once, and each card and row is set
// back where it was seen and runs to its new place. A card slides as a whole (its card element,
// never the sortable item dnd-kit measures), so the one that grew is uncovered by the next sliding
// down off it; a row slides inside its card (its cells, so dnd-kit's row stays put); a row new to a
// card fades in. Reduced motion drops it all.
//
// Marked up by the caller: `data-group` on each card's item (its card element first inside it) and
// `data-row` on each row's item. `capture()` reads where everything is seen; the hook also reads it
// after every layout, so a change made from anywhere starts from the last layout, and the drag
// handlers capture just before they change the plan (dnd-kit moves rows between renders).

type Snap = { top: number; scroll: number; cards: Map<string, number>; rows: Map<string, { card: string; rel: number }> };

const ID = "claims-flip";
const MS = 300;
const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";

const cardEl = (item: HTMLElement) => item.firstElementChild as HTMLElement | null;
const cells = (row: HTMLElement) => [...row.children] as HTMLElement[];

function measure(list: HTMLElement, scroller: HTMLElement | null): Snap {
  const top = list.getBoundingClientRect().top;
  const cards = new Map<string, number>();
  const rows = new Map<string, { card: string; rel: number }>();
  for (const item of list.querySelectorAll<HTMLElement>("[data-group]")) {
    const card = cardEl(item);
    if (!card || !item.offsetParent) continue;
    const cTop = card.getBoundingClientRect().top;
    cards.set(item.dataset.group!, cTop - top);
    for (const row of item.querySelectorAll<HTMLElement>("[data-row]")) {
      const cell = row.firstElementChild as HTMLElement | null;
      if (cell && row.offsetParent) rows.set(row.dataset.row!, { card: item.dataset.group!, rel: cell.getBoundingClientRect().top - cTop });
    }
  }
  return { top, scroll: scroller?.scrollTop ?? 0, cards, rows };
}

function slide(els: HTMLElement[], dy: number) {
  for (const el of els) el.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], { id: ID, duration: MS, easing: EASE });
}

// `scroller` finds the list's scrolling ancestor; `shiftRef` holds a scroll the panel made itself since
// the last layout (it moved what is seen, unlike a scroll of his, which moves nothing).
export function useClaimsFlip(list: RefObject<HTMLElement | null>, scroller: (el: HTMLElement) => HTMLElement, shiftRef: RefObject<number>, still: boolean) {
  const snap = useRef<Snap | null>(null);

  const capture = () => {
    const el = list.current;
    if (el) snap.current = measure(el, scroller(el));
  };

  useLayoutEffect(() => {
    const el = list.current;
    const before = snap.current;
    const ours = shiftRef.current;
    shiftRef.current = 0;
    if (!el) return;
    const s = scroller(el);
    // an interrupted change: `before` read where everything was seen mid-flight, so start from there
    for (const a of el.getAnimations({ subtree: true })) if (a.id === ID) a.cancel();
    const after = measure(el, s);
    snap.current = after;
    if (!before || still) return;
    // the cards opening after a group's drag: their bodies fade in as one (group-reveal), not row by row
    const opening = before.rows.size === 0;
    // where the list itself was seen, less any scroll of his since
    const lift = before.top - after.top - (after.scroll - before.scroll - ours);
    for (const item of el.querySelectorAll<HTMLElement>("[data-group]")) {
      const key = item.dataset.group!;
      const was = before.cards.get(key);
      const now = after.cards.get(key);
      const card = cardEl(item);
      if (was == null || now == null || !card) continue;
      const dy = was - now + lift;
      if (Math.abs(dy) >= 0.5) slide([card], dy);
      for (const row of item.querySelectorAll<HTMLElement>("[data-row]")) {
        const r = after.rows.get(row.dataset.row!);
        const w = before.rows.get(row.dataset.row!);
        if (!r) continue;
        if (!w || w.card !== key) {
          if (opening) continue;
          for (const c of cells(row)) c.animate([{ opacity: 0 }, { opacity: 1 }], { id: ID, duration: 200, delay: 80, easing: "ease-out", fill: "backwards" });
          continue;
        }
        const ry = w.rel - r.rel;
        if (Math.abs(ry) >= 0.5) slide(cells(row), ry);
      }
    }
  });

  return capture;
}
