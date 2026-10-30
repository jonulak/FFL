"use client";

import { useRouter } from "@/components/league-link";
import { useEffect } from "react";
import { movesStep } from "@/lib/notify";

// Re-renders a page that is not on the live hook (Transactions, Players) when a league move is logged: the API
// has rebuilt its pages by the time /live's moves count moves. That is caught on the move tick, or on the
// reconnect after the phone slept through it. Points ticks are ignored, so a game day does not reload these pages.
export function RefreshOnMoves() {
  const router = useRouter();
  useEffect(() => {
    let moves: number | null = null;
    const es = new EventSource("/api/live?moves=1");
    es.onmessage = (e) => {
      const step = movesStep(moves, e.data);
      moves = step.moves;
      if (step.refresh) router.refresh();
    };
    return () => es.close();
  }, [router]);
  return null;
}
