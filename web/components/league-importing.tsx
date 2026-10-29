"use client";

import { useEffect } from "react";
import { useRouter } from "./league-link";
import { Spinner } from "./motion";

const EVERY_MS = 3000;

// A page the API is still building for the first time (503 `building`: a league just added reads its whole season
// from ESPN on its first open). Not an error: it says so and asks again until the build lands, which then renders in
// its place. Paused while the page is hidden; asks at once on coming back.
export function LeagueImporting() {
  const router = useRouter();
  useEffect(() => {
    const ask = () => { if (document.visibilityState === "visible") router.refresh(); };
    const t = window.setInterval(ask, EVERY_MS);
    document.addEventListener("visibilitychange", ask);
    return () => { window.clearInterval(t); document.removeEventListener("visibilitychange", ask); };
  }, [router]);
  return (
    <div role="status" className="log-in max-w-prose space-y-2 py-6">
      <h1 className="flex items-center gap-2.5 font-display text-2xl font-bold tracking-tight">
        <Spinner on gap="" className="size-5" />Importing your league
      </h1>
      <p className="text-sm text-muted-foreground">
        Reading this season from ESPN for the first time: rosters, matchups and every move so far. This page opens by
        itself when it&apos;s ready, usually within a minute.
      </p>
    </div>
  );
}
