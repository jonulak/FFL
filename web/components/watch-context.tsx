"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { clientPost } from "@/app/api-client";
import type { WatchSet } from "@/lib/api";

// The watched players' ids (GET /watch/ids), read once by the layout and held here for every star in the app. A star
// takes at once and the API's answer settles that one id only, so two quick taps on two players never undo each
// other; a failed save puts it back and says so. Nothing here reaches ESPN or the decisions record.
// `names`: the name each star was tapped with, so the Watch page can name a player /watch has not priced yet.
// `sync`: a server answer's list (GET /watch, which unwatches a player now on my roster) replaces the ids, except any
// whose save is still in flight, so a tap made while the answer was on its way stands.
type Watch = {
  ids: Set<number>; names: ReadonlyMap<number, string>;
  toggle: (id: number, name?: string) => Promise<void>; sync: (ids: number[]) => void;
};

const Ctx = createContext<Watch | null>(null);

export function WatchProvider({ ids: initial, children }: { ids: number[]; children: React.ReactNode }) {
  const [ids, setIds] = useState(() => new Set(initial));
  const [names, setNames] = useState<ReadonlyMap<number, string>>(() => new Map());
  // a layout refresh (router.refresh) brings the server's list: it wins over what this page last held
  const [seen, setSeen] = useState(initial);
  if (seen !== initial) {
    setSeen(initial);
    setIds(new Set(initial));
  }
  const set = useCallback((id: number, on: boolean) => setIds((cur) => {
    if (cur.has(id) === on) return cur;
    const next = new Set(cur);
    if (on) next.add(id); else next.delete(id);
    return next;
  }), []);
  const saving = useRef(new Map<number, number>());
  const toggle = useCallback(async (id: number, name = "him") => {
    const on = !ids.has(id);
    if (on && name !== "him") setNames((cur) => new Map(cur).set(id, name));
    set(id, on);
    saving.current.set(id, (saving.current.get(id) ?? 0) + 1);
    try {
      set(id, (await clientPost<WatchSet>(`/watch/${id}`, { watched: on })).watched);
    } catch {
      set(id, !on);
      toast(`The watch on ${name} didn't save. Try again.`);
    } finally {
      const n = (saving.current.get(id) ?? 1) - 1;
      if (n > 0) saving.current.set(id, n); else saving.current.delete(id);
    }
  }, [ids, set]);
  const sync = useCallback((server: number[]) => setIds((cur) => {
    const next = new Set(server.filter((id) => !saving.current.has(id)));
    for (const id of saving.current.keys()) if (cur.has(id)) next.add(id);
    return next.size === cur.size && [...next].every((id) => cur.has(id)) ? cur : next;
  }), []);
  const value = useMemo(() => ({ ids, names, toggle, sync }), [ids, names, toggle, sync]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// null outside the provider (a test render): the stars then stay away rather than fail
export const useWatch = () => useContext(Ctx);
