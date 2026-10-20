"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import { toast } from "sonner";
import type { Card, DecisionRef, Roster } from "@/lib/api";
import { deferred, followedLineup, followedSit } from "@/lib/decisions";
import { afterFailure, applyOps, fillOptions, irIllegal, lineupFailure, type LineupFailure, type Move, movesFor, type Op, opIds, swapOptions, withoutBusy } from "@/lib/lineup";
import { useDecisionPrefs } from "./decision-prefs";
import { RationaleDialog } from "./rationale-dialog";
import { usePresets } from "./rationale-form";
import { type Notice, RosterRows, type SwapControl } from "./roster-cards";
import { type SheetPick, type SheetSubject, type SitControl, SwapSheet } from "./swap-sheet";
import { useFlip } from "./use-flip";
import { useLive } from "./use-live";

// The write moves players in ESPN and reads the lineup back: give it the claims submit's minute, not a GET's 20 s.
const MOVE_MS = 60_000;
// The "Why?" dialog opens once the rows' re-slot has played (use-flip: an 80 ms beat, then at most 560 ms).
const ASK_DELAY_MS = 700;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// Only the rows go live: the page header (outlook run, stale badge) is server state. The swap lives here: tap a
// player's swap button (or an empty starting slot) and the sheet lists the moves; a tap on one closes the sheet and
// re-slots the rows at once, before ESPN answers. Each move or sit is an op: the ops are posted one at a time in tap
// order (the API holds one lineup write at a time, and a later move may fill a slot an earlier one emptied), and
// while one saves only its own players are held: the rest of the roster can be moved or sat meanwhile. The rows
// shown are the ops still out laid over the last answer (lineup.applyOps), so ESPN's read-back or a live refetch
// replaces the answer and the ops still out lay over it again. A refusal drops its op, which puts its rows back,
// and says why under the row, except one ESPN probably took (`moved`), which keeps its rows and asks for a reload
// to confirm (lineup.afterFailure).
export function RosterLive({ initial }: { initial: Roster }) {
  const router = useRouter();
  const { data, replace } = useLive(initial, "/roster");
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [fill, setFill] = useState<string | null>(null);
  const [ops, setOps] = useState<Op[]>([]);
  const [errors, setErrors] = useState<({ id: number } & LineupFailure)[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [sitErrors, setSitErrors] = useState<{ id: number; text: string }[]>([]);
  const shown = useMemo(() => applyOps(data, ops), [data, ops]);
  const busy = useMemo(() => new Set(ops.flatMap(opIds)), [ops]);
  // the last answer, for a `moved` refusal to lay its moves over (it lands after other ops may have answered)
  const latest = useRef(data);
  useEffect(() => { latest.current = data; }, [data]);
  // the ops' posts, one after another
  const chain = useRef<Promise<void>>(Promise.resolve());
  const keys = useRef(0);
  const presets = usePresets();
  const prefs = useDecisionPrefs();
  const [asking, setAsking] = useState<{ kind: string; title: string; targets: Promise<number[]>; rationale?: string[] } | null>(null);
  // every change a move makes to the rows (the re-slot, ESPN's read-back, a refusal putting them back) plays out
  const { root, capture } = useFlip<HTMLDivElement>(shown);

  const pickedCard = useMemo(() => (picked == null ? null : shown.cards.find((c) => c.player_id === picked) ?? null), [shown.cards, picked]);
  const subject = useMemo((): SheetSubject | null => {
    if (pickedCard) return { kind: "card", card: pickedCard, options: withoutBusy(swapOptions(shown.cards, pickedCard, shown.empty_slots), busy) };
    if (fill) return { kind: "slot", slot: fill, options: withoutBusy(fillOptions(shown.cards, fill), busy) };
    return null;
  }, [pickedCard, fill, shown.cards, shown.empty_slots, busy]);

  const clear = () => { setErrors([]); setNotice(null); setSitErrors([]); };
  const pick = (c: Card) => { if (busy.has(c.player_id)) return; clear(); setFill(null); setPicked(c.player_id); };
  const openFill = (slot: string) => { clear(); setPicked(null); setFill(slot); };
  const close = () => { setPicked(null); setFill(null); };

  const names = (ids: number[]) => ids.map((i) => shown.cards.find((c) => c.player_id === i)?.name).join(", ");
  // The reason for a move, asked at the tap rather than after ESPN's answer: a dialog when it goes against the call,
  // a toast (with Edit) when it follows it. The answer then settles the prompt (`settle`): it hands the form the row
  // it recorded, opens the dialog after all if the server judged the move differently, and on a failed or
  // unrecorded move takes the prompt down (the move's own error says why under the row).
  // The dialog waits out the rows' re-slot (ASK_DELAY_MS) so the swap is seen before it is covered; a move refused
  // inside that wait never shows it. With logging off in Settings nothing is recorded, so nothing is asked.
  const ask = (kind: string, followed: boolean, dialogTitle: string, title: string) => {
    if (!prefs.log) return { settle() {}, fail() {} };
    const row = deferred<number[]>();
    const prompt = { kind, targets: row.promise };
    const edit = () => setAsking({ ...prompt, title, rationale: ["followed_call"] });
    const toastId = followed ? toast("Logged: followed the call", { action: { label: "Edit", onClick: edit } }) : null;
    const timer = followed ? null : setTimeout(() => setAsking({ ...prompt, title: dialogTitle }), ASK_DELAY_MS);
    const drop = () => {
      if (timer != null) clearTimeout(timer);
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

  // Show the op at once and queue its post behind the ones still out; `send` posts it and settles it.
  const run = (op: Op, send: () => Promise<void>) => {
    capture();
    setOps((o) => [...o, op]);
    const done = () => setOps((o) => o.filter((x) => x.key !== op.key));
    // a send that throws must not stall the ops behind it, their players held for good
    chain.current = chain.current.then(send).catch(() => {}).finally(done);
  };

  const move = (p: SheetPick) => {
    let moves: Move[], id: number;
    if (p.kind === "fill" && fill) {
      moves = [{ player_id: p.who.player_id, to_slot: fill }];
      id = p.who.player_id;
    } else if (p.kind !== "fill" && pickedCard) {
      moves = movesFor(pickedCard, p);
      id = pickedCard.player_id;
    } else return;
    if (moves.some((m) => busy.has(m.player_id))) return;
    close();
    clear();
    const who = names(moves.map((m) => m.player_id));
    const prompt = ask("lineup", followedLineup(moves, shown.cards), `Why move ${who}?`, who);
    run({ key: ++keys.current, kind: "move", moves }, async () => {
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
        if (f.moved) replace(afterFailure(f, latest.current, moves));
        setErrors((es) => [...es.filter((x) => x.id !== id), { id, ...f }]);
      }
    });
  };

  // Sit him for the week (or stand him back up): the switch and his chip flip at once; the API re-makes every call
  // without him and its answer replaces the rows. A refusal puts him back and says why under the switch.
  const setSat = (c: Card, sat: boolean) => {
    if (busy.has(c.player_id)) return;
    setSitErrors((es) => es.filter((x) => x.id !== c.player_id));
    const title = `${sat ? "Sit" : "Unsit"} ${c.name}`;
    const prompt = ask(sat ? "sit" : "unsit", followedSit(c, sat), title, title);
    run({ key: ++keys.current, kind: "sit", id: c.player_id, sat }, async () => {
      try {
        const back = await clientPost<Roster>(`/roster/sit/${c.player_id}`, { sat });
        capture();
        replace(back);
        prompt.settle(back.decision);
      } catch (e) {
        prompt.fail(e);
        capture();
        setSitErrors((es) => [...es.filter((x) => x.id !== c.player_id), { id: c.player_id, text: `Not saved: ${message(e)}` }]);
      }
    });
  };

  const reload = () => { setErrors([]); router.refresh(); };
  const sitting = ops.flatMap((o) => (o.kind === "sit" ? [o.id] : []));
  const swap: SwapControl = { picked: pickedCard, pending: [...busy], errors, pick, fill: openFill, reload };
  const sit: SitControl = { pending: sitting, errors: sitErrors, set: setSat };
  const held = pickedCard != null && busy.has(pickedCard.player_id);

  return (
    <>
      <RosterRows cards={shown.cards} empty={shown.empty_slots} week={shown.week} runBusy={shown.outlook_run.running}
        open={open} setOpen={setOpen} swap={swap} notice={notice} rootRef={root} />
      <SwapSheet subject={subject} move={move} onClose={close} blocked={irIllegal(shown.cards)} onFix={pick}
        week={shown.week} sit={sit} busy={held} />
      <RationaleDialog decision={asking} presets={presets} onClose={() => setAsking(null)} />
    </>
  );
}
