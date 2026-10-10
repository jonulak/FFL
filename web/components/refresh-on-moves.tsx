"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { isMovesTick } from "@/lib/notify";

// Re-renders a page that is not on the live hook (Transactions, Players) when a league move is logged: the API
// has rebuilt its pages by the time /live sends `m<n>`. Points ticks are ignored, so a game day does not reload
// these pages every few seconds.
export function RefreshOnMoves() {
  const router = useRouter();
  useEffect(() => {
    const es = new EventSource("/api/live");
    es.onmessage = (e) => {
      if (isMovesTick(e.data)) router.refresh();
    };
    return () => es.close();
  }, [router]);
  return null;
}
