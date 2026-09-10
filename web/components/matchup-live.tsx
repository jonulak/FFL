"use client";

import { useEffect, useState } from "react";
import { clientGet } from "@/app/api-client";
import type { Week } from "@/lib/api";
import { MatchupView } from "./matchup";

const POLL_MS = 60_000;

export function MatchupLive({ initial }: { initial: Week }) {
  const [data, setData] = useState(initial);
  // A server re-render (router.refresh(), navigation) hands us a fresh `initial`;
  // adjust state during render rather than in an effect, per the React docs.
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setData(initial);
  }

  // The clock the game phases are read against: fixed at first render (react-hooks/purity), then
  // advanced with each poll so a row turns live at kickoff without a reload.
  const [now, setNow] = useState(() => Date.now());

  const { live, week } = data;
  useEffect(() => {
    if (!live) return;
    const id = setInterval(
      () => clientGet<Week>(`/week/${week}`).then((d) => { setData(d); setNow(Date.now()); }).catch(() => {}),
      POLL_MS,
    );
    return () => clearInterval(id);
  }, [live, week]);

  return <MatchupView data={data} now={now} />;
}
