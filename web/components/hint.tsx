"use client";

import type { ReactElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

// An explanation on a focusable trigger (a button, a toggle, a link) in place of `title=`: it opens on
// hover and on keyboard focus, never on touch (Base UI), so the phone reads exactly as before. No text
// disables the tooltip but keeps the tree the same, so a site can pass a title that is sometimes
// undefined without remounting its child (focus and inner state survive).
export function Hint({ text, side = "top", children }: { text?: string | null; side?: "top" | "bottom"; children: ReactElement }) {
  return (
    <Tooltip disabled={!text}>
      <TooltipTrigger render={children} />
      <TooltipContent side={side}>{text}</TooltipContent>
    </Tooltip>
  );
}
