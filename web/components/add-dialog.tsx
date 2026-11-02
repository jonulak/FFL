"use client";

import { Lock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ClientError, clientPost } from "@/app/api-client";
import { useRouter } from "@/components/league-link";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import type { ClaimPlayer, DecisionRef } from "@/lib/api";
import { addCut, addTarget, cutBlock, SUBMIT_FLOOR_MS } from "@/lib/claims";
import { pts } from "@/lib/format";
import { useClaims } from "./claims-context";
import { PHONE_ACTION, PHONE_INSET } from "./claims-submit";
import { Spinner } from "./motion";
import { useDesk } from "./swap-sheet";

type Drop = number | "open";

// A free agent's quick add: pick who goes (or the open spot), then Add, and ESPN takes it now. No claims sheet. A
// player whose game has started can't be dropped for a free agent now, so his row offers the claim instead: a new
// claim group with this free agent and him as its cut. `onAdded` gets the decision the server recorded.
export function AddDialog({ id, name, open, onOpenChange, onAdded }: {
  id: number; name: string; open: boolean; onOpenChange: (o: boolean) => void; onAdded: (d: DecisionRef | null) => void;
}) {
  const desk = useDesk();
  // mounted from the first open on and kept, so the panel closes with its rows rather than empty; a fresh pick on reopen
  const [opens, setOpens] = useState(0);
  const [was, setWas] = useState(false);
  if (open !== was) {
    setWas(open);
    if (open) setOpens(opens + 1);
  }
  const body = opens > 0 && <Body key={opens} id={id} name={name} close={() => onOpenChange(false)} onAdded={onAdded}
    Title={desk ? DialogTitle : SheetTitle} Description={desk ? DialogDescription : SheetDescription} />;
  if (desk) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex max-h-[calc(var(--vv-h,100dvh)-4rem)] flex-col gap-3 rounded-lg p-4 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-md">
          {body}
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {/* PHONE_INSET: the layout sets no viewport-fit=cover, so the inset alone reads 0 in the in-app browser */}
      <SheetContent side="bottom" className={`max-h-[85dvh] gap-3 rounded-t-xl px-4 pt-4 ${PHONE_INSET}`}>
        {body}
      </SheetContent>
    </Sheet>
  );
}

function Body({ id, name, close, onAdded, Title, Description }: {
  id: number; name: string; close: () => void; onAdded: (d: DecisionRef | null) => void;
  Title: typeof DialogTitle; Description: typeof DialogDescription;
}) {
  const { data, groups, setGroups, refresh } = useClaims();
  const router = useRouter();
  const [pick, setPick] = useState<Drop | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<{ text: string; locked: boolean } | null>(null);
  // my roster, weakest first by adjusted ROS, as the claims' cut picker lists it
  const roster = Object.entries(data.players)
    .filter(([, p]) => p.droppable != null)
    .map(([k, p]) => [Number(k), p] as [number, ClaimPlayer])
    .sort((a, b) => (a[1].ros_adj ?? 0) - (b[1].ros_adj ?? 0));
  const dropName = pick === "open" ? null : pick != null ? data.players[String(pick)]?.name : null;

  const claimInstead = (drop: number) => {
    const g = addTarget(groups, "new", id);
    setGroups(addCut(g, g.length - 1, drop));
    toast(`${name} is on the claims sheet`, { description: `A new group, cutting ${data.players[String(drop)]?.name ?? "him"}` });
    close();
  };

  const send = async () => {
    if (pick == null) return;
    setSending(true);
    setError(null);
    try {
      const r = await clientPost<{ decision: DecisionRef | null }>("/roster/add", { add: id, drop: pick === "open" ? null : pick },
        SUBMIT_FLOOR_MS); // ESPN's write, then the roster pages rebuilt inline: well past the default 20 s at worst
      toast(data.writes ? `Added ${name}` : `Test mode: added ${name} (fake write)`, dropName ? { description: `Dropped ${dropName}` } : undefined);
      close();
      onAdded(r.decision);
      router.refresh(); // the roster changed: the table's comparisons and the pool
      void refresh();
    } catch (e) {
      const detail = e instanceof ClientError ? (e.body as { detail?: { error?: string; locked?: boolean } } | null)?.detail : null;
      // no answer at all (timeout, the phone left the app): ESPN may have taken it, so never say it failed
      const text = e instanceof ClientError ? detail?.error ?? e.message : "No answer from the server: the add may have happened. Reload to see the roster.";
      setError({ text, locked: !!detail?.locked });
    } finally {
      setSending(false);
    }
  };

  const row = (key: Drop, label: string, sub: string, block: string | null, locked: boolean) => {
    const on = pick === key;
    return (
      <li key={key} className="flex items-center gap-2">
        <button type="button" aria-pressed={on} disabled={!!block || locked || sending} onClick={() => { setPick(key); setError(null); }}
          className={`flex min-h-12 min-w-0 flex-1 items-center gap-3 rounded-md px-2.5 text-left outline-offset-2 transition-colors md:min-h-10 ${on ? "bg-foreground/[0.07] ring-1 ring-foreground/30" : "hover:bg-foreground/[0.04]"} disabled:cursor-default disabled:hover:bg-transparent`}>
          <span className={`min-w-0 flex-1 truncate font-medium ${block || locked ? "text-muted-foreground" : ""}`}>{label}</span>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{block ?? sub}</span>
        </button>
        {locked && typeof key === "number" && (
          <Button size="sm" variant="outline" disabled={sending} onClick={() => claimInstead(key)} className="shrink-0">
            <Lock aria-hidden />Claim instead
          </Button>
        )}
      </li>
    );
  };

  return (
    <>
      <div>
        <Title className="font-display text-lg font-semibold tracking-tight">Add {name}</Title>
        <Description className="text-sm text-muted-foreground">
          A free agent: ESPN adds him the moment you confirm. Pick who makes room.
        </Description>
      </div>
      <ul className="-mx-1 min-h-0 flex-1 overflow-y-auto px-1 py-0.5" aria-label="Who makes room">
        {data.open_spots > 0 && row("open", "Open roster spot", `${data.open_spots} open`, null, false)}
        {roster.map(([pid, p]) =>
          row(pid, p.name, p.locked ? "game started" : `${p.pos} · ROS ${pts(p.ros_adj)}`, cutBlock(p), !!p.locked && !cutBlock(p)))}
      </ul>
      {error && (
        <div role="alert" className="flex items-center gap-2 text-sm text-destructive">
          <p className="min-w-0 flex-1">{error.text}</p>
          {error.locked && typeof pick === "number" && (
            <Button size="sm" variant="outline" onClick={() => claimInstead(pick)}>Claim instead</Button>
          )}
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button variant="ghost" disabled={sending} onClick={() => { setGroups(addTarget(groups, "new", id)); close(); }} className={PHONE_ACTION}>
          Claim group instead
        </Button>
        <Button disabled={pick == null || sending} onClick={send} className={`ml-auto min-w-24 ${PHONE_ACTION}`}>
          <Spinner on={sending} />
          {pick == null ? "Pick a drop" : dropName ? `Add · drop ${dropName}` : "Add"}
        </Button>
      </div>
    </>
  );
}
