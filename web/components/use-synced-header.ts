"use client";

import { useLayoutEffect, useRef } from "react";

// The Players table scrolls sideways inside its own box on a narrow screen, and a box that scrolls cannot
// also hold a heading pinned to the page. So the heading is its own table in a box pinned under the nav,
// clipped, its scrollLeft kept to the body's, and its cells sized to the body's first row (a
// ResizeObserver re-measures when a column or the viewport changes). Both tables carry the same columns.
// Widths go on the heading cells, not on <col>s: a column a container query hides is display:none in
// both tables, which drops its cell from the row but not its <col>, and every col after it would shift.
// So only the cells that are drawn are paired, in order.
export function useSyncedHeader<H extends HTMLElement, B extends HTMLElement>(deps: unknown) {
  const head = useRef<H>(null);
  const body = useRef<B>(null);
  useLayoutEffect(() => {
    const h = head.current, b = body.current;
    const ht = h?.querySelector("table"), bt = b?.querySelector("table");
    if (!h || !b || !ht || !bt) return;
    const drawn = (el: Element) => el.getClientRects().length > 0;
    const measure = () => {
      const row = bt.querySelector<HTMLTableRowElement>("tbody > tr[data-key]");
      const ths = Array.from(ht.querySelectorAll<HTMLTableCellElement>("thead > tr:first-child > th")).filter(drawn);
      if (row) Array.from(row.cells).filter(drawn).forEach((td, i) => { if (ths[i]) ths[i].style.width = `${td.getBoundingClientRect().width}px`; });
      ht.style.width = `${bt.getBoundingClientRect().width}px`;
      sync();
    };
    // both boxes carry data-scrolled once the numbers have moved, for the pinned name's edge shadow
    const sync = () => {
      h.scrollLeft = b.scrollLeft;
      for (const el of [h, b]) el.toggleAttribute("data-scrolled", b.scrollLeft > 0);
    };
    measure();
    // the table's own box, and each cell of the first row: a column can change width while the table's doesn't
    const ro = new ResizeObserver(measure);
    ro.observe(bt);
    const row = bt.querySelector<HTMLTableRowElement>("tbody > tr[data-key]");
    if (row) for (const td of Array.from(row.cells)) ro.observe(td);
    b.addEventListener("scroll", sync, { passive: true });
    return () => { ro.disconnect(); b.removeEventListener("scroll", sync); };
  }, [deps]);
  return { head, body };
}
