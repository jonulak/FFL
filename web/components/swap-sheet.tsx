"use client";

import { ArrowDownToLine, LoaderCircle, Lock } from "lucide-react";
import { useState, useSyncExternalStore, type ReactNode } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import type { Card } from "@/lib/api";
import { pts, shortName } from "@/lib/format";
import { gainText, slotLabel, type LineupFailure, type SwapOption, type Target } from "@/lib/lineup";
import { PHONE_INSET } from "./claims-submit";
import { CallChip, Failure, Headshot, InjuryMark } from "./roster-cards";

// md and up gets the centred dialog; the phone and tablet get the bottom sheet. Closed on the server, so the
// first render never needs to know.
const DESK = "(min-width: 48rem)";
function useDesk() {
  return useSyncExternalStore(
    (on) => { const m = matchMedia(DESK); m.addEventListener("change", on); return () => m.removeEventListener("change", on); },
    () => matchMedia(DESK).matches,
    () => false,
  );
}

const SLOT = "font-display text-[0.75rem] font-semibold uppercase tracking-[0.06em] text-foreground/85 md:text-[0.8125rem] md:tracking-[0.12em]";
const optionKey = (o: SwapOption) => (o.kind === "swap" || o.kind === "locked" ? `p${o.with.player_id}` : o.kind === "slot" ? `s${o.slot}` : "bench");
const idle = (c: Card) => c.call === "BYE" || c.call === "OUT" || c.call === "IR";

// One option: where it lands (the slot), who or what is there, and what the move does to the lineup's projection.
function OptionRow({ o, picked, busy, disabled, onPick }: {
  o: SwapOption; picked: Card; busy: boolean; disabled: boolean; onPick: () => void;
}) {
  const locked = o.kind === "locked";
  const who = o.kind === "swap" || o.kind === "locked" ? o.with : null;
  const slot = o.kind === "slot" ? o.slot : o.kind === "bench" ? "BE" : who!.slot;
  const label = o.kind === "bench" ? `Move ${picked.name} to the bench`
    : o.kind === "slot" ? `Move ${picked.name} to the empty ${slotLabel(slot)}`
    : `Swap ${picked.name} with ${who!.name}, ${slotLabel(slot)}`;
  let shot: ReactNode, name: ReactNode, sub: ReactNode = null;
  if (who) {
    shot = <Headshot c={who} idle={idle(who) || locked} />;
    name = <><span className="md:hidden">{shortName(who.name)}</span><span className="max-md:hidden">{who.name}</span></>;
    sub = (
      <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <span className="font-semibold text-foreground/80">{who.pos}</span>
        <span className="tabular-nums">{pts(who.proj)} proj</span>
        {!locked && <CallChip c={who} />}
        {locked && <span>game started</span>}
      </span>
    );
  } else {
    shot = (
      <div aria-hidden className="flex size-10 items-center justify-center rounded-md border border-dashed border-foreground/25 text-muted-foreground">
        {o.kind === "bench" && <ArrowDownToLine className="size-4" />}
      </div>
    );
    name = o.kind === "bench" ? "To bench" : `Empty ${slotLabel(slot)}`;
  }
  const gain = o.kind === "locked" ? null : (o as Target).gain;
  return (
    <li>
      <button type="button" onClick={onPick} disabled={disabled || locked} aria-label={locked ? `${who!.name}: his game has started` : label}
        aria-busy={busy || undefined}
        className="grid min-h-14 w-full grid-cols-[2.25rem_2.5rem_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-md px-2 py-2 text-left outline-none transition-colors hover:bg-foreground/[0.05] focus-visible:ring-2 focus-visible:ring-ring active:bg-foreground/[0.08] disabled:pointer-events-none md:grid-cols-[3.25rem_2.5rem_minmax(0,1fr)_auto] md:min-h-12 [&:disabled:not([aria-busy])]:opacity-55">
        <span className={`${SLOT} ${slot === "BE" ? "text-muted-foreground" : ""}`}>{slot === "BE" ? "BE" : slotLabel(slot)}</span>
        {shot}
        <span className="min-w-0">
          <span className={`flex items-baseline gap-1.5 font-display text-[1.0625rem] font-semibold leading-tight tracking-tight ${who ? "" : "text-muted-foreground"}`}>
            <span className="min-w-0 truncate">{name}</span>
            {who && <InjuryMark c={who} always />}
          </span>
          {sub && <span className="mt-0.5 block">{sub}</span>}
        </span>
        <span className="flex min-w-12 justify-end">
          {busy
            ? <LoaderCircle aria-hidden className="size-5 animate-spin text-muted-foreground motion-reduce:animate-none" />
            : locked ? <Lock aria-hidden className="size-4 text-muted-foreground" />
            : (
              <span className={`font-display text-lg font-semibold tabular-nums ${gain! > 0 ? "text-foreground" : "text-muted-foreground"}`}>
                {gainText(gain!)}<span className="sr-only"> projected points</span>
              </span>
            )}
        </span>
      </button>
    </li>
  );
}

function Body({ picked, options, pending, error, move, reload, Title, Description }: {
  picked: Card; options: SwapOption[]; pending: number[]; error: LineupFailure | null;
  move: (t: Target) => void; reload: () => void;
  Title: typeof DialogTitle; Description: typeof DialogDescription;
}) {
  // the option tapped, for its spinner; any move in flight disables the rest
  const [tapped, setTapped] = useState<string | null>(null);
  const busy = pending.length > 0;
  const open = options.filter((o) => o.kind !== "locked").length;
  return (
    <>
      <div className="flex items-center gap-3 pr-10">
        <Headshot c={picked} idle={idle(picked)} />
        <div className="min-w-0">
          <Title className="truncate font-display text-xl font-bold leading-tight tracking-tight">Move {picked.name}</Title>
          <Description render={<div />} className="mt-0.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <span className="font-display font-semibold uppercase tracking-[0.08em] text-foreground/80">{slotLabel(picked.slot)}</span>
            <span aria-hidden>·</span>
            <span className="tabular-nums">{pts(picked.proj)} proj</span>
            <CallChip c={picked} />
            <InjuryMark c={picked} always />
          </Description>
        </div>
      </div>
      {error && <Failure f={error} reload={reload} />}
      <div className="-mx-2 min-h-0 overflow-y-auto overscroll-contain">
        {open === 0 && <p className="px-2 py-3 text-sm text-muted-foreground">Nowhere to move him: every slot he fits is taken by a player whose game has started.</p>}
        <ul className="space-y-0.5">
          {options.map((o) => {
            const k = optionKey(o);
            return (
              <OptionRow key={k} o={o} picked={picked} busy={busy && tapped === k} disabled={busy}
                onPick={() => { if (o.kind === "locked") return; setTapped(k); move(o); }} />
            );
          })}
        </ul>
      </div>
    </>
  );
}

// The swap sheet: the picked player's options, best change to the lineup first. A tap posts the move; the sheet
// closes when ESPN's read-back lands (RosterLive clears the pick) and shows a refusal in place.
export function SwapSheet({ picked, options, pending, error, move, reload, onClose }: {
  picked: Card | null; options: SwapOption[]; pending: number[]; error: LineupFailure | null;
  move: (t: Target) => void; reload: () => void; onClose: () => void;
}) {
  const desk = useDesk();
  // hold the last pick through the closing animation, when RosterLive has already cleared it
  const [shown, setShown] = useState<{ picked: Card; options: SwapOption[] } | null>(null);
  if (picked && (shown?.picked !== picked || shown.options !== options)) setShown({ picked, options });
  const open = picked != null;
  const onOpenChange = (o: boolean) => { if (!o && !pending.length) onClose(); };
  const body = shown && (
    <Body key={shown.picked.player_id} picked={shown.picked} options={shown.options} pending={pending} error={open ? error : null}
      move={move} reload={reload} Title={desk ? DialogTitle : SheetTitle} Description={desk ? DialogDescription : SheetDescription} />
  );
  if (desk) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent showCloseButton={!pending.length}
          className="flex max-h-[calc(100dvh-4rem)] flex-col gap-3 rounded-lg p-4 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-md">
          {body}
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {/* PHONE_INSET: the layout sets no viewport-fit=cover, so the inset alone reads 0 in the in-app browser */}
      <SheetContent side="bottom" showCloseButton={!pending.length}
        className={`max-h-[85dvh] gap-3 rounded-t-xl px-4 pt-4 ${PHONE_INSET}`}>
        {body}
      </SheetContent>
    </Sheet>
  );
}
