"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { clientGet } from "@/app/api-client";
import { generation } from "@/lib/generation";
import { leagueOf, withLeague } from "@/lib/league";

// A page's data kept live: the server render hands us `initial`; we always listen to the API's
// /live event stream and refetch `path` on every tick (a tick means a player's points, or a
// game's status, moved on ESPN's feed), so a page opened before kickoff goes live with the first
// game. An idle stream is a keepalive every 15 s. While the API says games are on, a slow 60 s
// poll also stands in, since the browser only retries a stream that cannot be opened (the NFL game pages
// pass a faster poll: their plays tick the stream only on fantasy points). A server re-render
// (router.refresh(), navigation) hands us a fresh `initial`, adopted during render.
const POLL_MS = 60_000;

// The newest data each path has fetched, and the server render it superseded. A back or
// forward navigation restores the page from the router cache with that same, now stale,
// `initial`; resuming from here instead keeps the plays since from replaying as new. A
// different `initial` is a fresh server render and wins.
const latest = new Map<string, { initial: unknown; data: unknown }>();
const resume = <T,>(initial: T, path: string): T => {
  const hit = latest.get(path);
  return hit && hit.initial === initial ? (hit.data as T) : initial;
};

export function useLive<T extends { live: boolean }>(initial: T, path: string, opts: { pollMs?: number } = {}): { data: T; now: number; replace: (d: T) => void } {
  const [data, setData] = useState(() => resume(initial, path));
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setData(resume(initial, path));
  }
  const from = useRef(initial);
  const gen = useRef(generation());
  useEffect(() => { from.current = initial; }, [initial]);
  // The clock game phases are read against: fixed at first render (react-hooks/purity), then
  // advanced with each update so a row turns live at kickoff without a reload.
  const [now, setNow] = useState(() => Date.now());

  const { live } = data;
  const refetchRef = useRef<() => void>(() => {});
  useEffect(() => {
    let closed = false;
    let inflight = false;
    let again = false;
    const refetch = () => {
      // one request at a time; a tick that lands mid-fetch queues exactly one more
      if (inflight) { again = true; return; }
      inflight = true;
      const ticket = gen.current.ticket();
      clientGet<T>(path)
        .then((d) => {
          // a replace() landed while this was in flight: this answer is from before it (a lineup move would snap back)
          if (closed || !gen.current.fresh(ticket)) return;
          latest.set(path, { initial: from.current, data: d });
          setData(d);
          setNow(Date.now());
        })
        .catch(() => {})
        .finally(() => { inflight = false; if (again && !closed) { again = false; refetch(); } });
    };
    refetchRef.current = refetch;
    // the page's own league: the stream ticks on that league's points alone (and on any league's move)
    const es = new EventSource(`/api${withLeague("/live", leagueOf(window.location.search))}`);
    let first = true;
    es.onmessage = () => {
      // the stream opens with the current version; only later messages are changes
      if (first) { first = false; return; }
      refetch();
    };
    return () => {
      closed = true;
      refetchRef.current = () => {};
      es.close();
    };
  }, [path]);

  // the fallback poll, and a refetch on coming back to the tab, only while games are on
  const ms = opts.pollMs ?? POLL_MS;
  useEffect(() => {
    if (!live) return;
    const tick = () => refetchRef.current();
    const poll = setInterval(tick, ms);
    const onVisible = () => { if (document.visibilityState === "visible") tick(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [live, ms]);

  // A fresher payload the page got some other way (a POST that answers with it) becomes the data, and the
  // one a back/forward navigation resumes from.
  const replace = useCallback((d: T) => {
    gen.current.bump();
    latest.set(path, { initial: from.current, data: d });
    setData(d);
    setNow(Date.now());
  }, [path]);

  return { data, now, replace };
}
