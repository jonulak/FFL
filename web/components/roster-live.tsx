"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ClientError, clientPost } from "@/app/api-client";
import type { Card, Roster } from "@/lib/api";
import { lineupFailure, type LineupFailure, movesFor, type Target, targets } from "@/lib/lineup";
import { type Notice, RosterRows, type SwapControl } from "./roster-cards";
import { useLive } from "./use-live";

// The write moves players in ESPN and reads the lineup back: give it the claims submit's minute, not a GET's 20 s.
const MOVE_MS = 60_000;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

// Only the rows go live: the page header (outlook run, stale badge) is server state. The swap lives here:
// tap a player's swap button to pick him, every row he can trade places with offers "Move here", and a tap on
// one posts the move. Nothing changes on the page until ESPN's answer comes back as the new data.
export function RosterLive({ initial }: { initial: Roster }) {
  const router = useRouter();
  const { data, replace } = useLive(initial, "/roster");
  const [open, setOpen] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [pending, setPending] = useState<number[]>([]);
  const [error, setError] = useState<({ id: number } & LineupFailure) | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  const pickedCard = picked == null ? null : data.cards.find((c) => c.player_id === picked) ?? null;
  const ts = pickedCard ? targets(data.cards, pickedCard, data.empty_slots) : [];

  // Esc, or a tap anywhere but a row (or the player dialog a name opened), puts the pick down.
  useEffect(() => {
    if (picked == null) return;
    const inDialog = () => document.querySelector("[role=dialog]") != null;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !inDialog()) setPicked(null); };
    const onDown = (e: PointerEvent) => {
      const el = e.target instanceof Element ? e.target : null;
      if (!el?.closest("[data-roster-row], [role=dialog]")) setPicked(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [picked]);

  const pick = (c: Card) => {
    if (pending.length) return;
    setError(null);
    setNotice(null);
    setPicked((p) => (p === c.player_id ? null : c.player_id));
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

  const swap: SwapControl = {
    picked: pickedCard, targets: ts, pending, error, pick, move,
    reload: () => { setError(null); router.refresh(); },
  };

  return (
    <RosterRows cards={data.cards} empty={data.empty_slots} week={data.week} runBusy={data.outlook_run.running}
      open={open} setOpen={setOpen} swap={swap} notice={notice} />
  );
}
