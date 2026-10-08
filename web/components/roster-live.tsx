"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import type { Card, Roster } from "@/lib/api";
import { afterFailure, applyMoves, fillOptions, irIllegal, lineupFailure, type LineupFailure, type Move, movesFor, swapOptions } from "@/lib/lineup";
import { type Notice, RosterRows, type SwapControl } from "./roster-cards";
import { type SheetPick, type SheetSubject, type SitControl, SwapSheet } from "./swap-sheet";
import { useFlip } from "./use-flip";
import { useLive } from "./use-live";

// The write moves players in ESPN and reads the lineup back: give it the claims submit's minute, not a GET's 20 s.
const MOVE_MS = 60_000;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// Only the rows go live: the page header (outlook run, stale badge) is server state. The swap lives here: tap a
// player's swap button (or an empty starting slot) and the sheet lists the moves; a tap on one closes the sheet and
// re-slots the rows at once, before ESPN answers. ESPN's read-back then replaces them; a refusal puts the page back
// as it was and says why under the row, except one ESPN probably took (`moved`), which keeps the new rows and asks
// for a reload to confirm (lineup.afterFailure).
export function RosterLive({ initial }: { initial: Roster }) {
  const router = useRouter();
  const { data, replace } = useLive(initial, "/roster");
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [fill, setFill] = useState<string | null>(null);
  const [pending, setPending] = useState<number[]>([]);
  // the page as the move will leave it, shown while it saves: a live refetch in that window reads the server's
  // pre-move page and must not snap the rows back
  const [optimistic, setOptimistic] = useState<Roster | null>(null);
  const [error, setError] = useState<({ id: number } & LineupFailure) | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [sitPending, setSitPending] = useState<number | null>(null);
  const [sitError, setSitError] = useState<{ id: number; text: string } | null>(null);
  const shown = optimistic ?? data;
  // every change a move makes to the rows (the re-slot, ESPN's read-back, a refusal putting them back) plays out
  const { root, capture } = useFlip<HTMLDivElement>(shown);

  const pickedCard = useMemo(() => (picked == null ? null : shown.cards.find((c) => c.player_id === picked) ?? null), [shown.cards, picked]);
  const subject = useMemo((): SheetSubject | null => {
    if (pickedCard) return { kind: "card", card: pickedCard, options: swapOptions(shown.cards, pickedCard, shown.empty_slots) };
    if (fill) return { kind: "slot", slot: fill, options: fillOptions(shown.cards, fill) };
    return null;
  }, [pickedCard, fill, shown.cards, shown.empty_slots]);

  const clear = () => { setError(null); setNotice(null); setSitError(null); };
  const pick = (c: Card) => { if (pending.length) return; clear(); setFill(null); setPicked(c.player_id); };
  const openFill = (slot: string) => { if (pending.length) return; clear(); setPicked(null); setFill(slot); };
  const close = () => { setPicked(null); setFill(null); };

  const move = async (p: SheetPick) => {
    if (pending.length || sitPending != null) return;
    let moves: Move[], id: number;
    if (p.kind === "fill" && fill) {
      moves = [{ player_id: p.who.player_id, to_slot: fill }];
      id = p.who.player_id;
    } else if (p.kind !== "fill" && pickedCard) {
      moves = movesFor(pickedCard, p);
      id = pickedCard.player_id;
    } else return;
    const before = shown;
    const next = applyMoves(before, moves);
    capture();
    // replace() also bumps the refetch generation: an answer to a GET sent before this tap is dropped
    replace(next);
    setOptimistic(next);
    setPending(moves.map((m) => m.player_id));
    close();
    clear();
    try {
      const back = await clientPost<Roster>("/lineup", { moves }, MOVE_MS);
      capture();
      replace(back);
      // a dev API with FFL_WRITES=0 answers as if ESPN had taken it; never let that read as ESPN
      setNotice(back.writes ? null : { id, text: "fake write (FFL_WRITES=0)" });
    } catch (e) {
      const f = e instanceof ClientError ? lineupFailure(e.status, e.body, e.message) : lineupFailure(null, null, message(e));
      capture();
      replace(afterFailure(f, before, next));
      setError({ id, ...f });
    } finally {
      setOptimistic(null);
      setPending([]);
    }
  };

  // Sit him for the week (or stand him back up): the switch and his chip flip at once; the API re-makes every call
  // without him and its answer replaces the rows. A refusal puts him back and says why under the switch.
  const setSat = async (c: Card, sat: boolean) => {
    if (sitPending != null || pending.length) return;
    setSitError(null);
    const before = shown;
    const call = sat ? (c.call === "START" || c.call === "BENCH" ? "SIT" : c.call) : (c.call === "SIT" ? c.model_call : c.call);
    capture();
    const next = { ...before, cards: before.cards.map((x) => (x.player_id === c.player_id ? { ...x, sat, call } : x)) };
    replace(next);
    setOptimistic(next);
    setSitPending(c.player_id);
    try {
      const back = await clientPost<Roster>(`/roster/sit/${c.player_id}`, { sat });
      capture();
      replace(back);
    } catch (e) {
      capture();
      replace(before);
      setSitError({ id: c.player_id, text: `Not saved: ${message(e)}` });
    } finally {
      setOptimistic(null);
      setSitPending(null);
    }
  };

  const reload = () => { setError(null); router.refresh(); };
  const swap: SwapControl = { picked: pickedCard, pending, error, pick, fill: openFill, reload };
  const sit: SitControl = { pending: sitPending, error: sitError, set: setSat };

  return (
    <>
      <RosterRows cards={shown.cards} empty={shown.empty_slots} week={shown.week} runBusy={shown.outlook_run.running}
        open={open} setOpen={setOpen} swap={swap} notice={notice} rootRef={root} />
      <SwapSheet subject={subject} move={move} onClose={close} blocked={irIllegal(shown.cards)} onFix={pick}
        week={shown.week} sit={sit} busy={pending.length > 0 || sitPending != null} />
    </>
  );
}
