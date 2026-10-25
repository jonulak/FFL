"use client";

import { createContext, useContext, type ReactNode } from "react";

// The league this document is open on (ffl-soc.4.13), from the root layout. `key` is null for home. A switch is a
// full page load (league-switcher.tsx), so the layout's value is always the page's. Every league opens every page
// (4.9); `key` is what league-link.tsx carries onto every in-app link.
type Open = { key: string | null; home: boolean };
const C = createContext<Open>({ key: null, home: true });

export function LeagueProvider({ league, children }: { league: string | null; children: ReactNode }) {
  return <C.Provider value={{ key: league, home: league == null }}>{children}</C.Provider>;
}

export const useOpenLeague = () => useContext(C);
