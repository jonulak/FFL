"use client";

import { Star } from "lucide-react";
import { useState } from "react";
import { Toggle } from "@/components/ui/toggle";
import { useWatch } from "./watch-context";

// The watch star (POST /watch/{id}): pressed, the watch pass looks at him every hour and pushes when he gets his
// chance (the Watch page lists him). Ink only, no gold (the Three Verdicts Rule): an outline at the row's control ink,
// filled at full ink when watched. The glyph is 14px in a 24px box that keeps the line it sits on; the tap target is
// an invisible pad around it, 40px on the phone and 32px from `sm`. On the dialog's band (`band`) it is white, the
// close button's register. The fill scales in on a tap, never on the first paint. `onToggle` hears the tap (the
// Watch page offers an undo when a row leaves).
export function WatchStar({ id, name, band = false, className = "", onToggle }: {
  id: number; name: string; band?: boolean; className?: string; onToggle?: (on: boolean) => void;
}) {
  const watch = useWatch();
  const [tapped, setTapped] = useState(false);
  if (!watch) return null;
  const on = watch.ids.has(id);
  return (
    <Toggle pressed={on} onPressedChange={() => { setTapped(true); void watch.toggle(id, name); onToggle?.(!on); }}
      aria-label={`Watch ${name}`} title={on ? `Watching ${name}: tap to stop` : `Watch ${name}`}
      className={`relative min-w-0 rounded-sm p-0 after:absolute after:content-[''] hover:bg-transparent aria-pressed:bg-transparent dark:hover:bg-transparent ${band
        ? "size-8 rounded-md text-white/80 after:-inset-1 hover:bg-white/15 hover:text-white aria-pressed:text-white aria-pressed:hover:bg-white/15 sm:after:inset-0"
        : "-my-1 size-6 text-foreground/55 after:-inset-2 hover:text-foreground aria-pressed:text-foreground sm:after:-inset-1"} ${className}`}>
      <Star key={String(on)} aria-hidden
        className={`${band ? "size-4" : "size-3.5"} stroke-[2.25] ${on ? "fill-current" : ""} ${tapped ? "fx-in" : ""}`} />
    </Toggle>
  );
}
