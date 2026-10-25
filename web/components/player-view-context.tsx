"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useState, useTransition, type ReactNode } from "react";
import type { PlayerView, Preset, ResolvedView, ViewSort } from "@/lib/api";
import { withLeague } from "@/lib/league";
import { isModified, playersHref as viewHref, POSITIONS, WATCH } from "@/lib/player-view";
import { PosTabs } from "./pos-tabs";

// The Players view the page is showing (ffl-8iz): the preset it starts from, the view as the API applied
// it, and the two ways to change it. `go` navigates (the server filters, so a new position, filter or
// column set is a fetch); a heading tap only re-sorts rows already here, so it rewrites the URL in place
// (history.replaceState, which Next keeps useSearchParams in step with) without a fetch, except on a
// capped view (see setSortLocal).
type Ctx = {
  q: string; league: string | null; presetId: string; preset: Preset; presets: Preset[]; defaultId: string;
  view: PlayerView; modified: boolean; pending: boolean;
  // base: the saved preset to diff against, when the provider's copy is behind (just saved, just created)
  go: (view: PlayerView, opts?: { presetId?: string; q?: string; base?: PlayerView }) => void;
  setSortLocal: (sort: ViewSort) => void;
  // a Players address for this view, naming the open league (lib/league.ts): every tab, search and sort keeps it
  playersHref: typeof viewHref;
};
const C = createContext<Ctx | null>(null);

export function PlayerViewProvider({ applied, presets, defaultId, q, league = null, capped = false, children }: {
  applied: ResolvedView; presets: Preset[]; defaultId: string; q: string; league?: string | null; capped?: boolean; children: ReactNode;
}) {
  const playersHref = useCallback<typeof viewHref>((...a) => withLeague(viewHref(...a), league), [league]);
  const router = useRouter();
  const [pending, start] = useTransition();
  const strip = ({ pos, fa, cols, filters, sort }: PlayerView): PlayerView => ({ pos, fa, cols, filters, sort });
  // state off a prop: a new server answer replaces whatever the last heading tap set
  const [seen, setSeen] = useState(applied);
  const [view, setView] = useState<PlayerView>(strip(applied));
  if (seen !== applied) {
    setSeen(applied);
    setView(strip(applied));
  }
  const preset = presets.find((p) => p.id === applied.preset) ?? presets[0];
  const go: Ctx["go"] = (next, opts = {}) => {
    const pid = opts.presetId ?? preset.id;
    const base = opts.base ?? presets.find((p) => p.id === pid) ?? preset;
    start(() => router.replace(playersHref(opts.q ?? q, pid, next, base), { scroll: false }));
  };
  const setSortLocal = (sort: ViewSort) => {
    const next = { ...view, sort };
    // the exception: a capped view (ALL or FLEX, no filter) is the server's first 150 by its sort, so the set
    // shown must match the sort. A new sort there is a fetch that re-cuts the 150, not a local re-sort.
    if (capped) return go(next);
    setView(next);
    window.history.replaceState(null, "", playersHref(q, preset.id, next, preset));
  };
  return (
    <C.Provider value={{ q, league, playersHref, presetId: preset.id, preset, presets, defaultId, view, modified: isModified(view, preset), pending, go, setSortLocal }}>
      {children}
    </C.Provider>
  );
}

export function usePlayerView(): Ctx {
  const c = useContext(C);
  if (!c) throw new Error("usePlayerView outside PlayerViewProvider");
  return c;
}

// The position strip on the Players page: ALL closes it, and a tab keeps the rest of the view (its
// columns, filters, sort) while dropping the search, as a tab tap always has. Watch, last, opens the watch list in
// the table's place and keeps the view for the tap back to a position.
export function PlayerPosTabs({ active }: { active: string | null }) {
  const { presetId, preset, view, playersHref } = usePlayerView();
  const hrefFor = (p: string) => p === WATCH ? `${playersHref("", presetId, view, preset)}&watch=1` : playersHref("", presetId, { ...view, pos: p }, preset);
  return <PosTabs active={active} base="/players" positions={[...POSITIONS, WATCH]} hrefFor={hrefFor} />;
}
