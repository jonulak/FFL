"use client";

import { ChevronDown, Settings } from "lucide-react";
import { useEffect, useState } from "react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { LeagueEntry } from "@/lib/api";
import { LEAGUE_ARRIVE, LEAGUE_COOKIE, withLeague } from "@/lib/league";

// The wordmark is the league switcher (mockup A, ffl-soc.4.13): "FFL ▾" lists every league (name · provider · format),
// the open one checked, each with a gear to its League settings. Picking one is a full page load, not router.push: the
// root layout (the watch stars, the decision switches) is read once per document for the league its URL names, and a
// soft navigation would keep the old league's. The cookie is set here too, so the start page ("/") opens on it; home
// forgets it. A league's settings open it the same way: its pages are under it.
function go(l: LeagueEntry, path: string) {
  // the marker carries the open league's name as it reads now and its width: the next page's name rolls over from it.
  // Read before anything re-renders, so it is the league being left.
  const el = document.querySelector(".league-name");
  const mark = { name: el?.textContent ?? "", w: Math.round(el?.getBoundingClientRect().width ?? 0) };
  // the open league's own settings (its gear) change no name: nothing to roll
  if (mark.name !== l.name) try { sessionStorage.setItem(LEAGUE_ARRIVE, JSON.stringify(mark)); } catch { /* storage off: the next page arrives as any load does */ }
  document.cookie = l.home ? `${LEAGUE_COOKIE}=; path=/; max-age=0` : `${LEAGUE_COOKIE}=${encodeURIComponent(l.key)}; path=/; max-age=${365 * 86400}; samesite=lax`;
  window.location.assign(l.home ? path : withLeague(path, l.key));
}

export function LeagueSwitcher({ leagues, open }: { leagues: LeagueEntry[]; open: LeagueEntry | null }) {
  // a league picked while its first page loads (a full load, a second or more cold): the open league's name breathes,
  // dimmed, so the pick reads as taken; the name changes only by the rollover on arrival. A page restored from the
  // back-forward cache drops it.
  const [pending, setPending] = useState<LeagueEntry | null>(null);
  useEffect(() => {
    const back = (e: PageTransitionEvent) => {
      if (e.persisted) setPending(null);
    };
    window.addEventListener("pageshow", back);
    return () => window.removeEventListener("pageshow", back);
  }, []);
  const pick = (key: string) => {
    const l = leagues.find((x) => x.key === key);
    if (!l || l.key === open?.key) return;
    go(l, "/"); // the marker first: it must read the name of the league being left
    setPending(l);
  };
  // the open league's name beside the wordmark, home's too, set on its baseline as the wordmark's subtitle: the pages
  // under it are that league's
  const shown = open;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={pending ? `FFL, opening ${pending.name}` : `FFL, league: ${shown?.name ?? "home"}. Switch league`} aria-busy={pending != null || undefined}
        className="group -ml-1 flex min-h-11 min-w-0 max-w-full items-center gap-1 rounded-md px-1 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span className="font-display text-[1.6rem] font-bold leading-none tracking-tight">FFL</span>
          {shown && (
            // the slot the name rolls over in on a league switch (globals.css): the old name's ::before over it
            <span className="league-roll min-w-0 max-w-[16rem] pb-px font-display text-[0.875rem] font-semibold uppercase leading-none tracking-[0.06em] text-foreground/75 sm:max-w-[9rem] lg:max-w-[13rem]">
              <span key={shown.key} title={shown.name} className={`league-name block truncate ${pending ? "league-wait" : ""}`}>
                {shown.name}
              </span>
            </span>
          )}
        </span>
        <ChevronDown aria-hidden className="league-chevron size-4 shrink-0 text-foreground/55 transition-transform duration-200 ease-(--ease-arrive) group-data-popup-open:rotate-180 motion-reduce:transition-none" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-auto min-w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel>League</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={open?.key ?? ""} onValueChange={(v) => pick(v as string)}>
            {leagues.map((l) => (
              <div key={l.key} className="flex items-stretch gap-0.5">
                <DropdownMenuRadioItem value={l.key} closeOnClick className="min-h-11 min-w-0 flex-1 py-1.5">
                  <span className="flex min-w-0 flex-col">
                    <span className="truncate font-medium">{l.name}</span>
                    <span className="text-xs text-muted-foreground">{[l.provider, l.format].filter(Boolean).join(" · ")}</span>
                  </span>
                </DropdownMenuRadioItem>
                <DropdownMenuItem aria-label={`League settings: ${l.name}`} onClick={() => go(l, "/settings/league")}
                  className="w-11 shrink-0 justify-center text-foreground/55 data-highlighted:text-foreground">
                  <Settings aria-hidden className="size-4" />
                </DropdownMenuItem>
              </div>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
