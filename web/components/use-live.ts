"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { clientGet } from "@/app/api-client";
import { generation } from "@/lib/generation";
import { leagueOf, withLeague } from "@/lib/league";
import { type LiveDelta, type LiveState, merge } from "@/lib/overlay";

// A page's data kept live: the server render hands us `initial`; we always listen to the API's
// /live event stream and refetch `path` on every tick (a tick means a player's points, or a
// game's status, moved on ESPN's feed), so a page opened before kickoff goes live with the first
// game. An idle stream is a keepalive every 15 s. While the API says games are on, a slow 60 s
// poll also stands in, since the browser only retries a stream that cannot be opened (the NFL game pages
// pass a faster poll: their plays tick the stream only on fantasy points). A server re-render
// (router.refresh(), navigation) hands us a fresh `initial`, adopted during render.
// A page that passes `overlay` (lib/overlay.ts) lays the stream's JSON ticks (live.delta: what changed in its
// league) on what it holds instead of refetching, and on every fetch after, so an answer built before a tick
// cannot undo it; a new week, a move or a bare tick still refetches whole. `side` is a small GET a tick also
// asks for (the Matchup board's plays strip), when its `path` names one.
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

type Opts<T> = {
  pollMs?: number;
  overlay?: (d: T, live: LiveState | null) => T;
  side?: { path: (d: T, delta: LiveDelta) => string | null; put: (d: T, got: unknown) => T };
};

export function useLive<T extends { live: boolean }>(initial: T, path: string, opts: Opts<T> = {}): { data: T; now: number; replace: (d: T) => void } {
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
  const cur = useRef(data);
  useEffect(() => { cur.current = data; }, [data]);
  const optsRef = useRef(opts);
  useEffect(() => { optsRef.current = opts; });

  const { live } = data;
  const refetchRef = useRef<() => void>(() => {});
  useEffect(() => {
    let closed = false;
    let inflight = false;
    let again = false;
    let state: LiveState | null = null; // this stream's ticks for the week, merged
    const show = (d: T) => {
      latest.set(path, { initial: from.current, data: d });
      cur.current = d;
      setData(d);
      setNow(Date.now());
    };
    const lay = (d: T) => optsRef.current.overlay?.(d, state) ?? d;
    const fetchSide = (sidePath: string) => {
      const ticket = gen.current.ticket();
      clientGet<unknown>(sidePath)
        .then((got) => {
          const put = optsRef.current.side?.put;
          if (!closed && put && gen.current.fresh(ticket)) show(put(cur.current, got));
        })
        .catch(() => {});
    };
    const refetch = () => {
      // one request at a time; a tick that lands mid-fetch queues exactly one more
      if (inflight) { again = true; return; }
      inflight = true;
      const ticket = gen.current.ticket();
      clientGet<T>(path)
        .then((d) => {
          // a replace() landed while this was in flight: this answer is from before it (a lineup move would snap back)
          if (closed || !gen.current.fresh(ticket)) return;
          show(lay(d));
        })
        .catch(() => {})
        .finally(() => { inflight = false; if (again && !closed) { again = false; refetch(); } });
    };
    refetchRef.current = refetch;
    // the page's own league: the stream ticks on that league's points alone (and on any league's move)
    const es = new EventSource(`/api${withLeague("/live", leagueOf(window.location.search))}`);
    let first = true;
    es.onmessage = (e: MessageEvent<string>) => {
      // the stream opens with the current version; only later messages are changes. A page that lays deltas on
      // refetches once on it while games are on: the stream counts from when it connected, so a point scored
      // between the page's GET and then would never reach it as a delta.
      const o = optsRef.current;
      if (first) { first = false; if (o.overlay && cur.current.live) refetch(); return; }
      if (!o.overlay || !e.data.startsWith("{")) { refetch(); return; }
      const delta = JSON.parse(e.data) as LiveDelta;
      const m = merge(state, delta);
      state = m.live;
      if (m.refetch) { refetch(); return; }
      show(lay(cur.current));
      const sidePath = o.side?.path(cur.current, delta);
      if (sidePath) fetchSide(sidePath);
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
    cur.current = d;
    setData(d);
    setNow(Date.now());
  }, [path]);

  return { data, now, replace };
}
