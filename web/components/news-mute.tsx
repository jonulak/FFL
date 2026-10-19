"use client";

import { Bell, BellOff } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { clientPost } from "@/app/api-client";
import { Toggle } from "@/components/ui/toggle";

const SAYS = "No pushes for him; his stories stay in News";

// The per-player push mute (POST /news/mute/{id}): pressed, the news desk sends no push for him. The press takes at
// once and the API's answer settles it; a failed save puts it back and says so. Muted, the line under it says what
// the mute does, on its own line; unmuted, the control's name does. `lead` stands at the start of the control's
// line (the dialog's way on to his news), so the line under it never squeezes it.
export function NewsMute({ id, muted: initial, name, lead, className = "" }: {
  id: number; muted: boolean; name: string; lead?: React.ReactNode; className?: string;
}) {
  const [muted, setMuted] = useState(initial);
  const [busy, setBusy] = useState(false);
  const flip = (on: boolean) => {
    setMuted(on);
    setBusy(true);
    clientPost<{ muted: boolean }>(`/news/mute/${id}`, { muted: on })
      .then((d) => setMuted(d.muted))
      .catch(() => {
        setMuted(!on);
        toast(`The mute for ${name} didn't save. Try again.`);
      })
      .finally(() => setBusy(false));
  };
  return (
    <div className={`flex flex-wrap items-center justify-end gap-x-3 gap-y-1 ${className}`}>
      {lead && <div className="mr-auto min-w-0">{lead}</div>}
      <Toggle variant="outline" pressed={muted} disabled={busy} onPressedChange={flip}
        aria-label={`Mute pushes for ${name}. ${SAYS}.`}
        className="h-10 gap-1.5 rounded-md border-foreground/15 px-3 font-display text-[0.8125rem] font-semibold uppercase tracking-[0.12em] text-foreground/70 hover:text-foreground sm:h-8 aria-pressed:border-foreground aria-pressed:bg-foreground aria-pressed:text-background aria-pressed:hover:bg-foreground/85">
        {muted ? <BellOff aria-hidden className="size-3.5" /> : <Bell aria-hidden className="size-3.5" />}
        {muted ? "Muted" : "Mute"}
      </Toggle>
      {muted && <p role="status" className="log-in basis-full text-right text-xs leading-4 text-muted-foreground">{SAYS}.</p>}
    </div>
  );
}
