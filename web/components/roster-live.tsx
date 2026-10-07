"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import type { Card, Roster } from "@/lib/api";
import { lineupFailure, type LineupFailure, movesFor, swapOptions, type Target } from "@/lib/lineup";
import { type Notice, RosterRows, type SwapControl } from "./roster-cards";
import { SwapSheet } from "./swap-sheet";
import { useLive } from "./use-live";

// The write moves players in ESPN and reads the lineup back: give it the claims submit's minute, not a GET's 20 s.
const MOVE_MS = 60_000;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// Only the rows go live: the page header (outlook run, stale badge) is server state. The swap lives here:
// tap a player's swap button to pick him, the swap sheet lists where he can go, and a tap on one posts the move.
// Nothing changes on the page until ESPN's answer comes back as the new data; then the sheet closes.
export function RosterLive({ initial }: { initial: Roster }) {
  const router = useRouter();
  const { data, replace } = useLive(initial, "/roster");
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [pending, setPending] = useState<number[]>([]);
  const [error, setError] = useState<({ id: number } & LineupFailure) | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const pickedCard = useMemo(() => (picked == null ? null : data.cards.find((c) => c.player_id === picked) ?? null), [data.cards, picked]);
  const options = useMemo(() => (pickedCard ? swapOptions(data.cards, pickedCard, data.empty_slots) : []), [data.cards, data.empty_slots, pickedCard]);

  const pick = (c: Card) => {
    if (pending.length) return;
    setError(null);
    setNotice(null);
    setPicked(c.player_id);
  };
  // the sheet closed: put the pick down; a refusal goes with it, a move ESPN took stays under his row
  const close = () => {
    setPicked(null);
    setError((e) => (e?.moved ? e : null));
  };

  const move = async (t: Target) => {
    if (!pickedCard || pending.length) return;
    const moves = movesFor(pickedCard, t);
    setPending(moves.map((m) => m.player_id));
    setError(null);
    setNotice(null);
    try {
      const next = await clientPost<Roster>("/lineup", { moves }, MOVE_MS);
      replace(next);
      setPicked(null);
      // a dev API with FFL_WRITES=0 answers as if ESPN had taken it; never let that read as ESPN
      setNotice(next.writes ? null : { id: pickedCard.player_id, text: "fake write (FFL_WRITES=0)" });
    } catch (e) {
      const f = e instanceof ClientError ? lineupFailure(e.status, e.body, e.message) : lineupFailure(null, null, message(e));
      setError({ id: pickedCard.player_id, ...f });
      // ESPN has the move; the page is only behind, so there is nothing left to pick
      if (f.moved) setPicked(null);
    } finally {
      setPending([]);
    }
  };

  const reload = () => { setError(null); router.refresh(); };
  const swap: SwapControl = { picked: pickedCard, options, pending, error, pick, move, reload };

  return (
    <>
      <RosterRows cards={data.cards} empty={data.empty_slots} week={data.week} runBusy={data.outlook_run.running}
        open={open} setOpen={setOpen} swap={swap} notice={notice} />
      <SwapSheet picked={pickedCard} options={options} pending={pending}
        error={error && error.id === picked ? error : null} move={move} reload={reload} onClose={close} />
    </>
  );
}
