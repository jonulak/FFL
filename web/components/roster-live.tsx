"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import { toast } from "sonner";
import type { Card, DecisionRef, Roster } from "@/lib/api";
import { deferred, followedLineup, followedSit } from "@/lib/decisions";
import { afterFailure, applyMoves, fillOptions, irIllegal, lineupFailure, type LineupFailure, type Move, movesFor, swapOptions } from "@/lib/lineup";
import { RationaleDialog } from "./rationale-dialog";
import { usePresets } from "./rationale-form";
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
  const presets = usePresets();
  const [asking, setAsking] = useState<{ kind: string; title: string; targets: Promise<number[]>; rationale?: string[] } | null>(null);
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

  const names = (ids: number[]) => ids.map((i) => shown.cards.find((c) => c.player_id === i)?.name).join(", ");
  // The reason for a move, asked at the tap rather than after ESPN's answer: a dialog when it goes against the call,
  // a toast (with Edit) when it follows it. The answer then settles the prompt (`settle`): it hands the form the row
  // it recorded, opens the dialog after all if the server judged the move differently, and on a failed or
  // unrecorded move takes the prompt down (the move's own error says why under the row).
  const ask = (kind: string, followed: boolean, dialogTitle: string, title: string) => {
    const row = deferred<number[]>();
    const prompt = { kind, targets: row.promise };
    const edit = () => setAsking({ ...prompt, title, rationale: ["followed_call"] });
    const toastId = followed ? toast("Logged: followed the call", { action: { label: "Edit", onClick: edit } }) : null;
    if (!followed) setAsking({ ...prompt, title: dialogTitle });
    const drop = () => {
      if (toastId != null) toast.dismiss(toastId);
      setAsking((a) => (a?.targets === row.promise ? null : a));
    };
    return {
      settle(ref: DecisionRef | null | undefined) {
        if (!ref) { row.resolve([]); drop(); return; }
        row.resolve([ref.id]);
        if (followed && ref.needs_rationale) { if (toastId != null) toast.dismiss(toastId); setAsking({ ...prompt, title: dialogTitle }); }
      },
      fail(e: unknown) { row.reject(e); drop(); },
    };
  };

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
    const who = names(moves.map((m) => m.player_id));
    const prompt = ask("lineup", followedLineup(moves, before.cards), `Why move ${who}?`, who);
    try {
      const back = await clientPost<Roster>("/lineup", { moves }, MOVE_MS);
      capture();
      replace(back);
      prompt.settle(back.decision);
      // a dev API with FFL_WRITES=0 answers as if ESPN had taken it; never let that read as ESPN
      setNotice(back.writes ? null : { id, text: "fake write (FFL_WRITES=0)" });
    } catch (e) {
      prompt.fail(e);
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
    const title = `${sat ? "Sit" : "Unsit"} ${c.name}`;
    const prompt = ask(sat ? "sit" : "unsit", followedSit(c, sat), title, title);
    try {
      const back = await clientPost<Roster>(`/roster/sit/${c.player_id}`, { sat });
      capture();
      replace(back);
      prompt.settle(back.decision);
    } catch (e) {
      prompt.fail(e);
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
      <RationaleDialog decision={asking} presets={presets} onClose={() => setAsking(null)} />
    </>
  );
}
