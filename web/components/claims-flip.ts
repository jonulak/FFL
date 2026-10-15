"use client";

import { Component, useLayoutEffect, useRef, type RefObject } from "react";

// The claim groups' changes, played out. Whatever moved a card or a row (the cards folding for a
// group's drag and opening after it, a row crossing into another group, a row removed, a card's
// errors arriving with a save) lays out at once, and each card and row is set back where it was
// seen and runs to its new place (FLIP). A card slides as a whole (its card element, never the
// sortable item dnd-kit measures) and an open one runs its height from the old to the new, so its
// foot keeps pace with what moves inside it; a row slides inside its card (its cells, so dnd-kit's
// row stays put), and so do the card's other pieces (`data-slide`, `data-fx`) and what follows the
// list in its panel (`data-trail`).
//
// What arrives fades in where it lands: a card rising a little, a row, an empty list's box, a
// card's errors or its "get K of N". What leaves fades out where it was: a copy of it is laid on
// the body (fixed, so no transformed sheet places it, and out of the way of the pointer) at the
// spot it was last seen, while what was under it closes up. Hidden is not gone: the bodies of
// folded cards keep their elements, so folding fades nothing out and opening fades nothing in
// (the bodies fade as one, group-reveal). Reduced motion drops it all.
//
// Marked up by the caller: `data-group` on each card's item (its card element first inside it),
// `data-row` on each row's item (`data-lifted` while it is the slot of the row being dragged) and
// `data-fx` (unique) on anything else that comes and goes. `capture()` reads where everything is
// seen; the caller renders `<BeforeCommit read={capture} />` beside the list, which reads it again
// just before every commit, so a change made from anywhere (the save's answer, the row control on
// the table) starts from what is on screen then. The reading after the last layout is not enough:
// what moved since without one (a page scroll under the sticky column, a notice above folding shut
// in CSS, a slide still running) would play back as a jump. The drag handlers capture too, just
// before they change the plan (dnd-kit moves rows between renders).

type Seen = { el: HTMLElement; rect: DOMRect; font?: [string, string, string, string] };
type Snap = {
  // the scroller read, kept: a later reading takes its scroll off the same element
  top: number; scroller: HTMLElement | null; scroll: number;
  cards: Map<string, number>; rows: Map<string, { card: string; rel: number }>;
  // a card's other pieces (`data-slide`, `data-fx` by its base), each against the card's body like a row
  blocks: Map<string, number>;
  // an open card's height (a folded one's is the fold's, and dnd-kit is measuring it), and what
  // follows the list in its panel (`data-trail`, the action bar) against the list's top
  heights: Map<string, number>; trails: Map<string, number>;
  seen: Map<string, Seen>;
};

const ID = "claims-flip";
const MS = 300;
const EASE = "cubic-bezier(0.16, 1, 0.3, 1)";

const cardEl = (item: HTMLElement) => item.firstElementChild as HTMLElement | null;
const cells = (row: HTMLElement) => [...row.children] as HTMLElement[];
const BLOCKS = "[data-slide], [data-fx]";
const blockKey = (card: string, el: HTMLElement) => `${card}:${(el.dataset.slide ?? el.dataset.fx ?? "").split("|")[0]}`;
// a row's text is set by its card, which its copy on the body would not have
const fontOf = (el: HTMLElement): Seen["font"] => {
  const s = getComputedStyle(el);
  return [s.fontSize, s.lineHeight, s.color, s.fontFamily];
};

function measure(list: HTMLElement, scroller: HTMLElement | null): Snap {
  const top = list.getBoundingClientRect().top;
  const cards = new Map<string, number>();
  const rows = new Map<string, { card: string; rel: number }>();
  const blocks = new Map<string, number>();
  const heights = new Map<string, number>();
  const trails = new Map<string, number>();
  const seen = new Map<string, Seen>();
  for (const item of list.querySelectorAll<HTMLElement>("[data-group]")) {
    const card = cardEl(item);
    if (!card || !item.offsetParent) continue;
    const rect = card.getBoundingClientRect();
    cards.set(item.dataset.group!, rect.top - top);
    seen.set(`g${item.dataset.group}`, { el: card, rect });
    // a row against the card's body, which carries his cells: the body's own fade-down as the cards
    // open (group-reveal) moves both, so it never reads as the row moving
    const body = card.querySelector<HTMLElement>(".group-body");
    const bTop = body ? body.getBoundingClientRect().top : rect.top;
    if (body?.offsetParent) heights.set(item.dataset.group!, rect.height);
    for (const row of item.querySelectorAll<HTMLElement>("[data-row]")) {
      const cell = row.firstElementChild as HTMLElement | null;
      if (!cell || !row.offsetParent) continue;
      rows.set(row.dataset.row!, { card: item.dataset.group!, rel: cell.getBoundingClientRect().top - bTop });
      // the lifted row's slot is drawn by the drag, not left behind as a copy
      if (!("lifted" in row.dataset)) seen.set(`r${row.dataset.row}`, { el: row, rect: row.getBoundingClientRect(), font: fontOf(row) });
    }
    for (const b of item.querySelectorAll<HTMLElement>(BLOCKS)) {
      if (b.offsetParent) blocks.set(blockKey(item.dataset.group!, b), b.getBoundingClientRect().top - bTop);
    }
  }
  for (const el of list.querySelectorAll<HTMLElement>("[data-fx]")) {
    if (el.offsetParent) seen.set(`f${el.dataset.fx}`, { el, rect: el.getBoundingClientRect(), font: fontOf(el) });
  }
  for (const t of list.closest("[data-flip-root]")?.querySelectorAll<HTMLElement>("[data-trail]") ?? []) {
    if (t.offsetParent) trails.set(t.dataset.trail!, t.getBoundingClientRect().top - top);
  }
  return { top, scroller, scroll: scroller?.scrollTop ?? 0, cards, rows, blocks, heights, trails, seen };
}

function slide(els: HTMLElement[], dy: number) {
  for (const el of els) el.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], { id: ID, duration: MS, easing: EASE });
}

function fadeIn(els: HTMLElement[], rise = 0) {
  const from: Keyframe = rise ? { opacity: 0, translate: `0 ${rise}px` } : { opacity: 0 };
  const to: Keyframe = rise ? { opacity: 1, translate: "0 0" } : { opacity: 1 };
  for (const el of els) el.animate([from, to], { id: ID, duration: rise ? MS : 220, delay: 60, easing: EASE, fill: "backwards" });
}

// What left, fading out where it was last seen.
function fadeOut({ el, rect, font }: Seen) {
  const copy = el.cloneNode(true) as HTMLElement;
  copy.removeAttribute("data-row");
  copy.removeAttribute("data-fx");
  copy.removeAttribute("id");
  copy.setAttribute("aria-hidden", "true");
  copy.inert = true;
  Object.assign(copy.style, {
    position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`,
    margin: "0", zIndex: "55", pointerEvents: "none", transform: "none", transition: "none", boxSizing: "border-box",
  });
  if (font) Object.assign(copy.style, { fontSize: font[0], lineHeight: font[1], color: font[2], fontFamily: font[3] });
  document.body.appendChild(copy);
  const a = copy.animate([{ opacity: 1 }, { opacity: 0, scale: "0.98" }], { duration: 200, easing: "ease-out", fill: "forwards" });
  a.finished.finally(() => copy.remove()).catch(() => {});
}

// `scroller` finds the list's scrolling ancestor; `shiftRef` holds a scroll the panel made itself
// since the last layout (it moved what is seen, unlike a scroll of his, which moves nothing).
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
    const root = el.closest<HTMLElement>("[data-flip-root]") ?? el;
    for (const a of root.getAnimations({ subtree: true })) if (a.id === ID) a.cancel();
    const after = measure(el, s);
    snap.current = after;
    if (!before || still) return;

    // `data-fx="base|variant"`: a new variant of what is still there (a count, an error that
    // changed) fades in over the old one rather than leaving a copy behind
    const bases = new Set([...after.seen.keys()].map((k) => k.split("|")[0]));
    for (const [key, was] of before.seen) {
      if (was.el.isConnected || after.seen.has(key) || bases.has(key.split("|")[0])) continue;
      // inside a card that left too: the card's copy carries it
      const item = key[0] === "g" ? null : was.el.closest<HTMLElement>("[data-group]");
      if (item && !after.cards.has(item.dataset.group!)) continue;
      fadeOut(was);
    }

    // the cards opening after a group's drag: their bodies fade in as one (group-reveal), not piece by piece
    const opening = before.rows.size === 0 && after.rows.size > 0;
    for (const [key, now] of after.seen) {
      if (key[0] === "f" && !before.seen.has(key) && !opening) fadeIn([now.el]);
    }

    // where the list itself was seen, less any scroll of his since, read off the scroller `before`
    // read: which ancestor scrolls can change in between (the docked column overflows for a frame
    // while a card rises in), and two scrollers' offsets differ by the page's scroll
    const scrolled = before.scroller ? before.scroller.scrollTop - before.scroll : 0;
    const lift = before.top - after.top - (scrolled - ours);
    // A card's height runs in layout, so what stands below it (the next cards, the action bar)
    // already rides its foot frame by frame: `above` is how much taller the cards above are at the
    // start than at the end, and each slide below leaves that part to the layout.
    let above = 0;
    for (const item of el.querySelectorAll<HTMLElement>("[data-group]")) {
      const key = item.dataset.group!;
      const was = before.cards.get(key);
      const now = after.cards.get(key);
      const card = cardEl(item);
      if (now == null || !card) continue;
      if (was == null) {
        fadeIn([card], 8);
        continue;
      }
      const dy = was - now + lift - above;
      if (Math.abs(dy) >= 0.5) slide([card], dy);
      // the card grows or shrinks with what slides inside it, so its foot never jumps ahead of them
      const wh = before.heights.get(key);
      const nh = after.heights.get(key);
      if (wh != null && nh != null && Math.abs(wh - nh) >= 0.5) {
        card.style.overflow = "clip";
        const a = card.animate([{ height: `${wh}px` }, { height: `${nh}px` }], { id: ID, duration: MS, easing: EASE });
        a.onfinish = a.oncancel = () => { card.style.overflow = ""; };
        above += wh - nh;
      }
      for (const row of item.querySelectorAll<HTMLElement>("[data-row]")) {
        const r = after.rows.get(row.dataset.row!);
        const w = before.rows.get(row.dataset.row!);
        if (!r) continue;
        if (!w || w.card !== key) {
          if (!opening) fadeIn(cells(row));
          continue;
        }
        const ry = w.rel - r.rel;
        if (Math.abs(ry) >= 0.5) slide(cells(row), ry);
      }
      // what stands below a row that came or went (a list's heading, the cut picker, the errors)
      // slides with the rows rather than jumping by a row's height
      for (const b of item.querySelectorAll<HTMLElement>(BLOCKS)) {
        const k = blockKey(key, b);
        const w = before.blocks.get(k);
        const r = after.blocks.get(k);
        if (w == null || r == null) continue;
        if (Math.abs(w - r) >= 0.5) slide([b], w - r);
      }
    }
    // and what follows the list in the panel moves with its foot
    for (const t of root.querySelectorAll<HTMLElement>("[data-trail]")) {
      const w = before.trails.get(t.dataset.trail!);
      const r = after.trails.get(t.dataset.trail!);
      if (w != null && r != null && Math.abs(w - r - above) >= 0.5) slide([t], w - r - above);
    }
  });

  return capture;
}

// Calls `read` before React changes the DOM, on every update: getSnapshotBeforeUpdate runs for the
// whole tree before any mutation, and no hook does. Draws nothing.
export class BeforeCommit extends Component<{ read: () => void }> {
  getSnapshotBeforeUpdate() {
    this.props.read();
    return null;
  }
  componentDidUpdate() {}
  render() {
    return null;
  }
}
