"use client";

import { useCallback, useLayoutEffect, useRef } from "react";

// A lineup move, played out (FLIP): `capture()` reads where every row stands just before the page changes, and once
// React has laid out the new page each row is set back where it stood and runs to its new place. A player who
// changed slot travels as a lifted card (`data-travel`) and keeps an ink wash that fades (`data-landed`); his
// neighbours slide to make room; a group that moved slides with them and a card that grew or shrank follows, all on
// one clock so the board holds together. Reduced motion keeps the wash and drops the travel.
//
// Marked up by the caller: `data-flip-group` on each group, `data-flip-list` (its key) on the group's card,
// `data-flip` (unique) and `data-slot` on each row.

type Snap = { at: number; rows: Map<string, { top: number; slot: string }>; groups: Map<string, number>; lists: Map<string, number> };

const ID = "flip";
// a beat before the board moves, so the sheet that asked for the move has started to leave
const BEAT = 80;
// a snapshot older than this belongs to a change that never rendered
const STALE_MS = 1000;

// Page coordinates, so a scroll between the capture and the new layout moves nothing.
function measure(root: HTMLElement): Snap {
  const y = window.scrollY;
  const rows = new Map<string, { top: number; slot: string }>();
  const groups = new Map<string, number>();
  const lists = new Map<string, number>();
  for (const el of root.querySelectorAll<HTMLElement>("[data-flip]")) {
    rows.set(el.dataset.flip!, { top: el.getBoundingClientRect().top + y, slot: el.dataset.slot ?? "" });
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-flip-group]")) groups.set(el.dataset.flipGroup!, el.getBoundingClientRect().top + y);
  for (const el of root.querySelectorAll<HTMLElement>("[data-flip-list]")) lists.set(el.dataset.flipList!, el.getBoundingClientRect().height);
  return { at: performance.now(), rows, groups, lists };
}

// The longer the trip, the longer it takes, within a routine change's range.
const duration = (dist: number) => Math.round(Math.min(560, Math.max(320, 280 + dist * 0.3)));

function land(el: HTMLElement) {
  // restart the wash when he moves again before it has faded
  el.removeAttribute("data-landed");
  void el.offsetWidth;
  el.setAttribute("data-landed", "");
}

function play(root: HTMLElement, before: Snap) {
  // an interrupted move: the capture read where everything stood mid-flight, so start from there
  for (const a of root.getAnimations({ subtree: true })) if (a.id === ID) a.cancel();
  const after = measure(root);
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const rows = [...root.querySelectorAll<HTMLElement>("[data-flip]")].map((el) => {
    const key = el.dataset.flip!;
    const was = before.rows.get(key);
    const group = el.closest<HTMLElement>("[data-flip-group]")?.dataset.flipGroup ?? "";
    // his travel inside his group: the group's own slide carries the rest
    const shift = (before.groups.get(group) ?? after.groups.get(group)!) - after.groups.get(group)!;
    const dy = was ? was.top - after.rows.get(key)!.top - shift : 0;
    return { el, was, dy, moved: !!was && was.slot !== (el.dataset.slot ?? "") };
  });
  for (const r of rows) if (r.moved) land(r.el);
  if (still) return;

  const ms = duration(Math.max(0, ...rows.filter((r) => r.moved).map((r) => Math.abs(r.dy))));
  const timing: KeyframeAnimationOptions = { id: ID, duration: ms, delay: BEAT, easing: "cubic-bezier(0.16, 1, 0.3, 1)", fill: "backwards" };
  const running: Animation[] = [];

  for (const el of root.querySelectorAll<HTMLElement>("[data-flip-group]")) {
    const key = el.dataset.flipGroup!;
    const was = before.groups.get(key);
    if (was === undefined) {
      running.push(el.animate([{ opacity: 0 }, { opacity: 1 }], { ...timing, duration: 200, delay: BEAT + ms / 2 }));
      continue;
    }
    const dy = was - after.groups.get(key)!;
    if (Math.abs(dy) >= 0.5) running.push(el.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], timing));
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-flip-list]")) {
    const was = before.lists.get(el.dataset.flipList!);
    const now = after.lists.get(el.dataset.flipList!)!;
    if (was !== undefined && Math.abs(was - now) >= 0.5) running.push(el.animate([{ height: `${was}px` }, { height: `${now}px` }], timing));
  }
  for (const { el, was, dy, moved } of rows) {
    if (!was) {
      // a row that was not on the board (an empty slot left behind) settles in as the rest arrive
      running.push(el.animate([{ opacity: 0 }, { opacity: 1 }], { ...timing, duration: 200, delay: BEAT + ms / 2 }));
      continue;
    }
    if (Math.abs(dy) < 0.5) continue;
    if (moved) el.setAttribute("data-travel", "");
    const a = el.animate([{ translate: `0 ${dy}px` }, { translate: "0 0" }], timing);
    if (moved) a.finished.finally(() => el.removeAttribute("data-travel")).catch(() => {});
    running.push(a);
  }
  if (running.length === 0) return;
  // the cards stop clipping while anyone is between them
  root.setAttribute("data-flipping", "");
  Promise.allSettled(running.map((a) => a.finished)).then(() => {
    if (!root.getAnimations({ subtree: true }).some((a) => a.id === ID && a.playState === "running")) root.removeAttribute("data-flipping");
  });
}

// `dep` is the page the rows are drawn from: a capture plays out on the first layout after it changes.
export function useFlip<E extends HTMLElement>(dep: unknown) {
  const root = useRef<E>(null);
  const snap = useRef<Snap | null>(null);
  const capture = useCallback(() => {
    if (root.current) snap.current = measure(root.current);
  }, []);
  useLayoutEffect(() => {
    const el = root.current, before = snap.current;
    snap.current = null;
    if (el && before && performance.now() - before.at < STALE_MS) play(el, before);
  }, [dep]);
  return { root, capture };
}
