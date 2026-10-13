"use client";

import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { Toggle } from "@/components/ui/toggle";
import { playersHref } from "@/lib/players";
import { Presence, Spinner } from "./motion";

// The Free agents filter: pressed (the default) shows the waiver pool at the tab's position,
// released adds every rostered player there. It is URL state like the tab (fa=0 when released),
// so a back button or a shared link lands on the same view. A search spans every player whatever
// it says, so while one runs the chip steps back and says so rather than hiding.
export function FaToggle({ fa, pos, q }: { fa: boolean; pos: string; q: string }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const searching = q.trim() !== "";
  return (
    <div className="flex items-center gap-2.5">
      <Toggle
        variant="outline"
        pressed={fa}
        onPressedChange={(on) => startTransition(() => router.replace(playersHref(q, pos, on), { scroll: false }))}
        aria-busy={pending}
        title={fa ? "showing free agents; release to add every rostered player" : "showing every player; press for free agents only"}
        className={`h-10 gap-1.5 rounded-md border-foreground/15 px-3 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/70 hover:text-foreground sm:h-8 aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:hover:bg-foreground/85 ${searching ? "opacity-55" : ""}`}
      >
        <Spinner on={pending} className="size-3.5" />
        Free agents
      </Toggle>
      <Presence show={searching}><span className="text-xs text-muted-foreground">search covers every player</span></Presence>
    </div>
  );
}
