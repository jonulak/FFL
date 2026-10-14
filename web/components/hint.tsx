"use client";

import type { ReactElement } from "react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

// An explanation on a focusable trigger (a button, a toggle, a link) in place of `title=`: it opens on
// hover and on keyboard focus, never on touch (Base UI), so the phone reads exactly as before. No text
// renders the child alone, so a site can pass a title that is sometimes undefined.
export function Hint({ text, side = "top", children }: { text?: string | null; side?: "top" | "bottom"; children: ReactElement }) {
  if (!text) return children;
  return (
    <Tooltip>
      <TooltipTrigger render={children} />
      <TooltipContent side={side}>{text}</TooltipContent>
    </Tooltip>
  );
}
