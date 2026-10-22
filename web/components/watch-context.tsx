"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { toast } from "sonner";
import { clientPost } from "@/app/api-client";
import type { WatchSet } from "@/lib/api";

// The watched players' ids (GET /watch/ids), read once by the layout and held here for every star in the app. A star
// takes at once and the API's answer settles that one id only, so two quick taps on two players never undo each
// other; a failed save puts it back and says so. Nothing here reaches ESPN or the decisions record.
type Watch = { ids: Set<number>; toggle: (id: number, name?: string) => Promise<void> };

const Ctx = createContext<Watch | null>(null);

export function WatchProvider({ ids: initial, children }: { ids: number[]; children: React.ReactNode }) {
  const [ids, setIds] = useState(() => new Set(initial));
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
  const toggle = useCallback(async (id: number, name = "him") => {
    const on = !ids.has(id);
    set(id, on);
    try {
      set(id, (await clientPost<WatchSet>(`/watch/${id}`, { watched: on })).watched);
    } catch {
      set(id, !on);
      toast(`The watch on ${name} didn't save. Try again.`);
    }
  }, [ids, set]);
  const value = useMemo(() => ({ ids, toggle }), [ids, toggle]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// null outside the provider (a test render): the stars then stay away rather than fail
export const useWatch = () => useContext(Ctx);
