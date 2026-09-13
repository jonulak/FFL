"use client";

import { useEffect, useState } from "react";
import { clientGet } from "@/app/api-client";

// A page's data kept live: the server render hands us `initial`; while the API says games are
// on we listen to its /live event stream and refetch `path` on every tick (a tick means a
// player's points moved on ESPN's feed). If the stream cannot be opened the browser keeps
// retrying it, and a slow 60 s poll stands in so the page still moves. A server re-render
// (router.refresh(), navigation) hands us a fresh `initial`, adopted during render.
const POLL_MS = 60_000;

export function useLive<T extends { live: boolean }>(initial: T, path: string): { data: T; now: number } {
  const [data, setData] = useState(initial);
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setData(initial);
  }
  // The clock game phases are read against: fixed at first render (react-hooks/purity), then
  // advanced with each update so a row turns live at kickoff without a reload.
  const [now, setNow] = useState(() => Date.now());

  const { live } = data;
  useEffect(() => {
    if (!live) return;
    let closed = false;
    let inflight = false;
    let again = false;
    const refetch = () => {
      // one request at a time; a tick that lands mid-fetch queues exactly one more
      if (inflight) { again = true; return; }
      inflight = true;
      clientGet<T>(path)
        .then((d) => { if (!closed) { setData(d); setNow(Date.now()); } })
        .catch(() => {})
        .finally(() => { inflight = false; if (again && !closed) { again = false; refetch(); } });
    };
    const es = new EventSource("/api/live");
    let first = true;
    es.onmessage = () => {
      // the stream opens with the current version; only later messages are changes
      if (first) { first = false; return; }
      refetch();
    };
    const poll = setInterval(refetch, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") refetch(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      closed = true;
      es.close();
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [live, path]);

  return { data, now };
}
