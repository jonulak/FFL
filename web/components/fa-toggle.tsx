"use client";

import { Toggle } from "@/components/ui/toggle";
import { usePlayerView } from "./player-view-context";
import { Presence, Spinner } from "./motion";
import { Hint } from "./hint";

// The Free agents filter: pressed (the default) shows the waiver pool at the tab's position,
// released adds every rostered player there. It is URL state like the tab (fa=0 when released),
// so a back button or a shared link lands on the same view. A search spans every player whatever
// it says, so while one runs the chip steps back and says so rather than hiding. The rest of the view
// (position, columns, filters, sort, search) rides along.
export function FaToggle() {
  const { view, go, pending, q } = usePlayerView();
  const fa = view.fa;
  const searching = q.trim() !== "";
  return (
    <div className="flex items-center gap-2.5">
      <Hint text={fa ? "showing free agents; release to add every rostered player" : "showing every player; press for free agents only"}>
        <Toggle
          variant="outline"
          pressed={fa}
          onPressedChange={(on) => go({ ...view, fa: on })}
          aria-busy={pending}
          className={`h-10 gap-1.5 rounded-md border-foreground/15 px-3 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/70 hover:text-foreground sm:h-8 aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:hover:bg-foreground/85 ${searching ? "opacity-55" : ""}`}
        >
          <Spinner on={pending} className="size-3.5" />
          Free agents
        </Toggle>
      </Hint>
      <Presence show={searching}><span className="text-xs text-muted-foreground">search covers every player</span></Presence>
    </div>
  );
}
