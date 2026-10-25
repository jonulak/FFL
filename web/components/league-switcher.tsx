"use client";

import { ChevronDown } from "lucide-react";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuGroup, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { LeagueEntry } from "@/lib/api";
import { LEAGUE_COOKIE, withLeague } from "@/lib/league";

// The wordmark is the league switcher (mockup A, ffl-soc.4.13): "FFL ▾" lists every league (name · provider · format),
// the open one checked. Picking one is a full page load, not router.push: the root layout (the watch stars, the
// decision switches) is read once per document for the league its URL names, and a soft navigation would keep the
// old league's. The cookie is set here too, so the start page ("/") opens on it; home forgets it.
export function LeagueSwitcher({ leagues, open }: { leagues: LeagueEntry[]; open: LeagueEntry | null }) {
  const pick = (key: string) => {
    const l = leagues.find((x) => x.key === key);
    if (!l || l.key === open?.key) return;
    document.cookie = l.home ? `${LEAGUE_COOKIE}=; path=/; max-age=0` : `${LEAGUE_COOKIE}=${encodeURIComponent(l.key)}; path=/; max-age=${365 * 86400}; samesite=lax`;
    window.location.assign(l.home ? "/" : withLeague("/", l.key));
  };
  // the open league's name beside the wordmark, home's too: the pages under it are that league's
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label={`FFL, league: ${open?.name ?? "home"}. Switch league`}
        className="-ml-1 flex min-h-11 min-w-0 max-w-full items-center gap-1 rounded-md px-1 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        <span className="font-display text-[1.6rem] font-bold leading-none tracking-tight">FFL</span>
        {open && <span title={open.name} className="min-w-0 max-w-[16rem] truncate sm:max-w-[9rem] font-display text-sm font-semibold uppercase tracking-[0.08em] text-foreground/70">{open.name}</span>}
        <ChevronDown aria-hidden className="size-4 text-foreground/55" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-auto min-w-64">
        <DropdownMenuGroup>
          <DropdownMenuLabel>League</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={open?.key ?? ""} onValueChange={(v) => pick(v as string)}>
            {leagues.map((l) => (
              <DropdownMenuRadioItem key={l.key} value={l.key} className="min-h-11 py-1.5">
                <span className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">{l.name}</span>
                  <span className="text-xs text-muted-foreground">{[l.provider, l.format].filter(Boolean).join(" · ")}</span>
                </span>
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
