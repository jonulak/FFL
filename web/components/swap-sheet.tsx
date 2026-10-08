"use client";

import { ArrowDownToLine, BriefcaseMedical, LoaderCircle, Lock } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import type { Card } from "@/lib/api";
import { pts, shortName } from "@/lib/format";
import { accepts, type Fill, type FillOption, gainText, slotLabel, type SwapOption, type Target } from "@/lib/lineup";
import { PHONE_INSET } from "./claims-submit";
import { CallChip, Headshot, InjuryMark, IrBlocked } from "./roster-cards";

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
const idle = (c: Card) => c.call === "BYE" || c.call === "OUT" || c.call === "IR" || c.call === "SIT";

// The sit toggle's state, owned by RosterLive: who is saving and the last refusal.
export type SitControl = { pending: number | null; error: { id: number; text: string } | null; set: (c: Card, sat: boolean) => void };

// Sit him for the week: out of the lineup call (the chip reads SIT, the rest of the roster is called without him) until
// ESPN's week turns. Nothing is sent to ESPN; where he stands there is the slot's business.
function SitToggle({ c, week, sit, disabled }: { c: Card; week: number; sit: SitControl; disabled: boolean }) {
  const busy = sit.pending === c.player_id;
  const error = sit.error?.id === c.player_id ? sit.error.text : null;
  const id = `sit-${c.player_id}`;
  return (
    <div className="border-y border-foreground/10 py-1">
      <label htmlFor={id} className="flex min-h-14 cursor-pointer items-center gap-3 rounded-md px-2 transition-colors hover:bg-foreground/[0.04] has-[[data-disabled]]:cursor-default has-[[data-disabled]]:hover:bg-transparent md:min-h-12">
        <span className="min-w-0 flex-1">
          <span className="block font-display text-[1.0625rem] font-semibold leading-tight tracking-tight">Sit this week</span>
          <span className="mt-0.5 block text-xs text-pretty text-muted-foreground">
            {c.sat ? `Out of the lineup call for week ${week}` : "Leave him out of the lineup call"}{" "}
            {/* the dot travels with its clause, so a wrapped line never ends on it */}
            <span className="whitespace-nowrap"><span aria-hidden>· </span>ESPN unchanged</span>
          </span>
        </span>
        {busy && <LoaderCircle aria-hidden className="size-4 animate-spin text-muted-foreground motion-reduce:animate-none" />}
        <Switch id={id} className="data-unchecked:bg-foreground/20 dark:data-unchecked:bg-foreground/25" checked={c.sat} disabled={disabled || busy} onCheckedChange={(on) => sit.set(c, on)} />
      </label>
      {error && <p role="alert" className="px-2 pb-1.5 text-sm text-out">{error}</p>}
    </div>
  );
}

// What the sheet is for: a picked player (where can he go) or an empty starting slot (who can fill it).
export type SheetSubject =
  | { kind: "card"; card: Card; options: SwapOption[] }
  | { kind: "slot"; slot: string; options: FillOption[] };
// A tappable row: a move for the picked player, or a player to put in the empty slot.
export type SheetPick = Target | Fill;

type Row = { key: string; slot: string; who: Card | null; label: string; gain: number | null; pick: SheetPick | null };

const lockedRow = (c: Card): Row => ({ key: `p${c.player_id}`, slot: c.slot, who: c, label: `${c.name}: his game has started`, gain: null, pick: null });

function cardRows(picked: Card, options: SwapOption[]): Row[] {
  return options.map((o): Row => {
    if (o.kind === "locked") return lockedRow(o.with);
    if (o.kind === "bench") return { key: "bench", slot: "BE", who: null, label: `Move ${picked.name} to the bench`, gain: o.gain, pick: o };
    if (o.kind === "slot") {
      const to = o.slot === "IR" ? "IR" : `the empty ${slotLabel(o.slot)}`;
      return { key: `s${o.slot}`, slot: o.slot, who: null, label: `Move ${picked.name} to ${to}`, gain: o.gain, pick: o };
    }
    return { key: `p${o.with.player_id}`, slot: o.with.slot, who: o.with, gain: o.gain, pick: o,
      label: `Swap ${picked.name} with ${o.with.name}, ${slotLabel(o.with.slot)}` };
  });
}

// The fill sheet's slot column says where each man comes from: where he goes is the sheet's title.
function fillRows(slot: string, options: FillOption[]): Row[] {
  return options.map((o): Row => o.kind === "locked" ? lockedRow(o.with)
    : { key: `p${o.who.player_id}`, slot: o.who.slot, who: o.who, gain: o.gain, pick: o,
        label: `Move ${o.who.name} from ${o.who.slot === "BE" ? "the bench" : slotLabel(o.who.slot)} to ${slotLabel(slot)}` });
}

// One option: the slot, who or what is there, and what the move does to the lineup's projection.
function OptionRow({ r, onPick }: { r: Row; onPick: () => void }) {
  const locked = r.pick == null;
  const who = r.who;
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
        {r.slot === "BE" && <ArrowDownToLine className="size-4" />}
        {r.slot === "IR" && <BriefcaseMedical className="size-4" />}
      </div>
    );
    name = r.slot === "BE" ? "To bench" : r.slot === "IR" ? "To IR" : `Empty ${slotLabel(r.slot)}`;
  }
  return (
    <li>
      <button type="button" onClick={onPick} disabled={locked} aria-label={r.label}
        className="grid min-h-14 w-full grid-cols-[1.75rem_2.5rem_minmax(0,1fr)_auto] items-center gap-x-2.5 rounded-md px-2 py-2 text-left outline-none transition-colors hover:bg-foreground/[0.05] focus-visible:ring-2 focus-visible:ring-ring active:bg-foreground/[0.08] disabled:pointer-events-none disabled:opacity-55 md:grid-cols-[3.25rem_2.5rem_minmax(0,1fr)_auto] md:min-h-12">
        <span className={`${SLOT} ${r.slot === "BE" ? "text-muted-foreground" : ""}`}>{r.slot === "BE" ? "BE" : slotLabel(r.slot)}</span>
        {shot}
        <span className="min-w-0">
          <span className={`flex items-baseline gap-1.5 font-display text-[1.0625rem] font-semibold leading-tight tracking-tight ${who ? "" : "text-muted-foreground"}`}>
            <span className="min-w-0 truncate">{name}</span>
            {who && <InjuryMark c={who} />}
          </span>
          {sub && <span className="mt-0.5 block">{sub}</span>}
        </span>
        <span className="flex min-w-12 justify-end">
          {locked || r.gain == null ? <Lock aria-hidden className="size-4 text-muted-foreground" /> : (
            <span className={`font-display text-lg font-semibold tabular-nums ${r.gain > 0 ? "text-foreground" : "text-muted-foreground"}`}>
              {gainText(r.gain)}<span className="sr-only"> projected points</span>
            </span>
          )}
        </span>
      </button>
    </li>
  );
}

function Body({ subject, move, blocked, onFix, week, sit, busy, Title, Description }: {
  subject: SheetSubject; move: (p: SheetPick) => void; blocked: Card | null; onFix: (c: Card) => void;
  week: number; sit: SitControl; busy: boolean; Title: typeof DialogTitle; Description: typeof DialogDescription;
}) {
  const rows = subject.kind === "card" ? cardRows(subject.card, subject.options) : fillRows(subject.slot, subject.options);
  const open = rows.filter((r) => r.pick).length;
  let header: ReactNode;
  if (subject.kind === "card") {
    const picked = subject.card;
    header = (
      <>
        <Headshot c={picked} idle={idle(picked)} />
        <div className="min-w-0">
          <Title className="truncate font-display text-xl font-bold leading-tight tracking-tight">Move {picked.name}</Title>
          <Description render={<div />} className="mt-0.5 flex items-center gap-1.5 text-sm text-muted-foreground">
            <span className="font-display font-semibold uppercase tracking-[0.08em] text-foreground/80">{slotLabel(picked.slot)}</span>
            <span aria-hidden>·</span>
            <span className="tabular-nums">{pts(picked.proj)} proj</span>
            <CallChip c={picked} />
            <InjuryMark c={picked} />
          </Description>
        </div>
      </>
    );
  } else {
    header = (
      <>
        <div aria-hidden className="size-10 shrink-0 rounded-md border border-dashed border-foreground/25" />
        <div className="min-w-0">
          <Title className="truncate font-display text-xl font-bold leading-tight tracking-tight">Fill {slotLabel(subject.slot)}</Title>
          <Description className="mt-0.5 text-sm text-muted-foreground">
            {subject.slot === "IR"
              ? <>Empty IR slot <span aria-hidden>·</span> IR or Out only</>
              : <>Empty starting slot <span aria-hidden>·</span> takes {[...accepts(subject.slot)].join(", ")}</>}
          </Description>
        </div>
      </>
    );
  }
  // while an IR player has no designation, anyone else's sheet says so first and offers only the move that fixes it
  const block = blocked && !(subject.kind === "card" && subject.card.player_id === blocked.player_id) ? blocked : null;
  const none = subject.kind === "card"
    ? "Nowhere to move him: every slot he fits is taken by a player whose game has started."
    : subject.slot === "IR"
      ? "Nobody can go on IR: only a player ESPN lists as IR or Out can, and nobody on the roster is."
      : "Nobody can fill it: every player who fits is already in this slot or his game has started.";
  return (
    <>
      <div className="flex items-center gap-3 pr-10">{header}</div>
      {block && <IrBlocked c={block} onFix={() => onFix(block)} disabled={false} />}
      {/* an IR man is out of the call already */}
      {subject.kind === "card" && subject.card.slot !== "IR" && (
        <SitToggle c={subject.card} week={week} sit={sit} disabled={busy} />
      )}
      <div className="-mx-2 min-h-0 overflow-y-auto overscroll-contain">
        {open === 0 && !block && <p className="px-2 py-3 text-sm text-muted-foreground">{none}</p>}
        <ul className="space-y-0.5">
          {rows.map((r) => <OptionRow key={r.key} r={r} onPick={() => { if (r.pick) move(r.pick); }} />)}
        </ul>
      </div>
    </>
  );
}

// The swap sheet: the picked player's options, or who can fill an empty slot, best change to the lineup first. A
// tap posts the move and closes the sheet at once: the page shows the move before ESPN answers (RosterLive).
export function SwapSheet({ subject, move, onClose, blocked, onFix, week, sit, busy }: {
  subject: SheetSubject | null; move: (p: SheetPick) => void; onClose: () => void;
  blocked: Card | null; onFix: (c: Card) => void; week: number; sit: SitControl; busy: boolean;
}) {
  const desk = useDesk();
  // hold the last subject through the closing animation, when RosterLive has already cleared it
  const [shown, setShown] = useState<SheetSubject | null>(null);
  if (subject && shown !== subject) setShown(subject);
  const open = subject != null;
  // Opened by a tap or a click, focus goes to the panel, not the first option: iOS Safari rings a focused button even
  // after a touch, so the best option opened framed as if picked. A keyboard still lands on that option. The sheet is
  // opened from RosterLive's state, not a Base UI trigger, so Base UI cannot tell how (its type comes through empty):
  // the last input on the page says.
  const panel = useRef<HTMLDivElement>(null);
  const byKey = useRef(false);
  useEffect(() => {
    const pointer = () => { byKey.current = false; };
    const key = (e: KeyboardEvent) => { if (e.key === "Enter" || e.key === " ") byKey.current = true; };
    document.addEventListener("pointerdown", pointer, true);
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("pointerdown", pointer, true); document.removeEventListener("keydown", key, true); };
  }, []);
  const initialFocus = () => (byKey.current ? true : panel.current);
  const onOpenChange = (o: boolean) => { if (!o) onClose(); };
  const body = shown && (
    <Body key={shown.kind === "card" ? `p${shown.card.player_id}` : `s${shown.slot}`} subject={shown} move={move}
      blocked={blocked} onFix={onFix} week={week} sit={sit} busy={busy} Title={desk ? DialogTitle : SheetTitle} Description={desk ? DialogDescription : SheetDescription} />
  );
  if (desk) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent ref={panel} initialFocus={initialFocus} className="flex max-h-[calc(100dvh-4rem)] flex-col gap-3 rounded-lg p-4 shadow-[0_24px_48px_-20px_rgb(0_0_0_/_0.6)] sm:max-w-md">
          {body}
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      {/* PHONE_INSET: the layout sets no viewport-fit=cover, so the inset alone reads 0 in the in-app browser */}
      <SheetContent ref={panel} initialFocus={initialFocus} side="bottom" className={`max-h-[85dvh] gap-3 rounded-t-xl px-4 pt-4 ${PHONE_INSET}`}>
        {body}
      </SheetContent>
    </Sheet>
  );
}
